"""Tests for the gatekeeper review system.

Covers:
- DB schema v4 fields (rejection_count, pr_number, pr_url)
- review_reject_completion() in db.py
- review_reject_task() in queue_utils.py
- get_review_feedback() in queue_utils.py
- claim_task() prioritization of rejected tasks
- review_utils.py (init, record, complete, pass/fail checks)
- _db_task_to_file_format() includes new fields
- process_gatekeeper_reviews() in scheduler
- approve_and_merge() in queue_utils.py
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# =============================================================================
# DB Schema v4 Tests
# =============================================================================


class TestSchemaV4:
    """Tests for DB schema v4 fields."""

    def test_new_columns_exist_on_create(self, initialized_db):
        """Test that new columns are present in freshly-created tasks."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            create_task(task_id="v4test", file_path="/v4.md")
            task = get_task("v4test")

            assert task["rejection_count"] == 0
            assert task["pr_number"] is None
            assert task["pr_url"] is None

    def test_rejection_count_and_attempt_count_independent(self, initialized_db):
        """Test that rejection_count and attempt_count are independent counters."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import (
                create_task, claim_task, submit_completion,
                reject_completion, review_reject_completion, get_task,
            )

            create_task(task_id="indep1", file_path="/indep1.md")

            # Pre-check rejection (increments attempt_count)
            claim_task()
            submit_completion("indep1", commits_count=0)
            reject_completion("indep1", reason="no commits")

            task = get_task("indep1")
            assert task["attempt_count"] == 1
            assert task["rejection_count"] == 0

            # Review rejection (increments rejection_count)
            claim_task()
            submit_completion("indep1", commits_count=1)
            review_reject_completion("indep1", reason="bad code", reviewer="gk")

            task = get_task("indep1")
            assert task["attempt_count"] == 1  # unchanged
            assert task["rejection_count"] == 1  # incremented

    def test_pr_number_and_url_can_be_set(self, initialized_db):
        """Test that pr_number and pr_url can be updated on tasks."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, get_task

            create_task(task_id="pr1", file_path="/pr1.md")
            update_task("pr1", pr_number=42, pr_url="https://github.com/test/repo/pull/42")

            task = get_task("pr1")
            assert task["pr_number"] == 42
            assert task["pr_url"] == "https://github.com/test/repo/pull/42"

    def test_migration_v3_to_v4(self, mock_config, db_path):
        """Test migration from v3 to v4 adds new columns."""
        with patch('orchestrator.db.get_database_path', return_value=db_path):
            from orchestrator.db import get_connection, migrate_schema, get_schema_version, SCHEMA_VERSION

            # Manually create v3 schema (without new columns)
            with get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        file_path TEXT NOT NULL UNIQUE,
                        queue TEXT NOT NULL DEFAULT 'incoming',
                        priority TEXT DEFAULT 'P2',
                        complexity TEXT,
                        role TEXT,
                        branch TEXT DEFAULT 'main',
                        blocked_by TEXT,
                        claimed_by TEXT,
                        claimed_at DATETIME,
                        commits_count INTEGER DEFAULT 0,
                        turns_used INTEGER,
                        attempt_count INTEGER DEFAULT 0,
                        has_plan BOOLEAN DEFAULT FALSE,
                        plan_id TEXT,
                        project_id TEXT,
                        auto_accept BOOLEAN DEFAULT FALSE,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS schema_info (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_info (key, value) VALUES (?, ?)",
                    ("version", "3"),
                )
                # Also create required tables
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS task_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id TEXT NOT NULL,
                        event TEXT NOT NULL,
                        agent TEXT,
                        details TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS agents (
                        name TEXT PRIMARY KEY,
                        role TEXT,
                        running BOOLEAN DEFAULT FALSE,
                        pid INTEGER,
                        current_task_id TEXT,
                        last_run_start DATETIME,
                        last_run_end DATETIME
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS projects (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        description TEXT,
                        status TEXT DEFAULT 'draft',
                        branch TEXT,
                        base_branch TEXT DEFAULT 'main',
                        auto_accept BOOLEAN DEFAULT FALSE,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        created_by TEXT,
                        completed_at DATETIME
                    )
                """)

                # Insert a task under v3
                conn.execute("""
                    INSERT INTO tasks (id, file_path, queue)
                    VALUES ('pre_migration', '/pre.md', 'incoming')
                """)

            # Run migration
            migrated = migrate_schema()
            assert migrated is True

            # Verify new columns exist and defaults work
            with get_connection() as conn:
                cursor = conn.execute("SELECT * FROM tasks WHERE id = 'pre_migration'")
                row = dict(cursor.fetchone())
                assert row["rejection_count"] == 0
                assert row["pr_number"] is None
                assert row["pr_url"] is None

            assert get_schema_version() == SCHEMA_VERSION


# =============================================================================
# Review Reject Completion Tests (db.py)
# =============================================================================


class TestReviewRejectCompletion:
    """Tests for review_reject_completion() in db.py."""

    def test_increments_rejection_count(self, initialized_db):
        """Test that review_reject_completion increments rejection_count."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, review_reject_completion, get_task

            create_task(task_id="rr1", file_path="/rr1.md")
            review_reject_completion("rr1", reason="needs fixes", reviewer="gk-arch")

            task = get_task("rr1")
            assert task["rejection_count"] == 1
            assert task["queue"] == "incoming"
            assert task["claimed_by"] is None

    def test_multiple_rejections(self, initialized_db):
        """Test that multiple rejections increment correctly."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, review_reject_completion, get_task

            create_task(task_id="rr2", file_path="/rr2.md")
            review_reject_completion("rr2", reason="fix1")
            review_reject_completion("rr2", reason="fix2")
            review_reject_completion("rr2", reason="fix3")

            task = get_task("rr2")
            assert task["rejection_count"] == 3

    def test_logs_history_event(self, initialized_db):
        """Test that review rejection logs a history event."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, review_reject_completion, get_task_history

            create_task(task_id="rr3", file_path="/rr3.md")
            review_reject_completion("rr3", reason="code quality", reviewer="gk-testing")

            history = get_task_history("rr3")
            review_events = [h for h in history if h["event"] == "review_rejected"]
            assert len(review_events) == 1
            assert review_events[0]["agent"] == "gk-testing"


# =============================================================================
# Review Reject Task Tests (queue_utils.py)
# =============================================================================


class TestReviewRejectTask:
    """Tests for review_reject_task() in queue_utils.py."""

    def test_reject_appends_feedback_to_file(self, mock_config, initialized_db):
        """Test that review_reject_task appends feedback to the task file."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task
            from orchestrator.queue_utils import review_reject_task

            # Create task file
            prov_dir = mock_config / "shared" / "queue" / "provisional"
            prov_dir.mkdir(parents=True, exist_ok=True)
            task_path = prov_dir / "TASK-rrfb1.md"
            task_path.write_text("# [TASK-rrfb1] Test task\n\n## Context\nSome context.\n")

            create_task(task_id="rrfb1", file_path=str(task_path))

            new_path, action = review_reject_task(
                task_path,
                feedback="Architecture violation: don't access engine internals.",
                rejected_by="gk-architecture",
            )

            assert action == "rejected"
            content = new_path.read_text()
            assert "## Review Feedback (rejection #1)" in content
            assert "Architecture violation" in content

    def test_escalation_after_max_rejections(self, mock_config, initialized_db):
        """Test that review_reject_task escalates after max rejections."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.message_utils.warning') as mock_warn:
                from orchestrator.db import create_task, update_task
                from orchestrator.queue_utils import review_reject_task

                prov_dir = mock_config / "shared" / "queue" / "provisional"
                prov_dir.mkdir(parents=True, exist_ok=True)
                task_path = prov_dir / "TASK-esc1.md"
                task_path.write_text("# [TASK-esc1] Test task\n")

                create_task(task_id="esc1", file_path=str(task_path))
                # Set rejection_count to 2 (next will be 3 = escalation at max_rejections=3)
                update_task("esc1", rejection_count=2)

                new_path, action = review_reject_task(
                    task_path,
                    feedback="Still broken.",
                    rejected_by="gk-testing",
                    max_rejections=3,
                )

                assert action == "escalated"
                assert "escalated" in str(new_path)
                mock_warn.assert_called_once()

    def test_rejection_preserves_branch(self, mock_config, initialized_db):
        """Test that review rejection preserves the task's branch setting."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task
            from orchestrator.queue_utils import review_reject_task

            prov_dir = mock_config / "shared" / "queue" / "provisional"
            prov_dir.mkdir(parents=True, exist_ok=True)
            task_path = prov_dir / "TASK-branch1.md"
            task_path.write_text("# [TASK-branch1] Test\n")

            create_task(task_id="branch1", file_path=str(task_path), branch="feature/test")

            review_reject_task(task_path, "needs work", rejected_by="gk")

            task = get_task("branch1")
            assert task["branch"] == "feature/test"  # Branch preserved

    def test_reject_preserves_original_content(self, mock_config, initialized_db):
        """Test that rejection preserves original task file content (header, context, criteria)."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task
            from orchestrator.queue_utils import review_reject_task

            prov_dir = mock_config / "shared" / "queue" / "provisional"
            prov_dir.mkdir(parents=True, exist_ok=True)
            original_content = (
                "# [TASK-pres1] Important feature\n\n"
                "ROLE: implement\n"
                "PRIORITY: P1\n\n"
                "## Context\n"
                "This task implements feature X with detailed requirements.\n\n"
                "## Acceptance Criteria\n"
                "- [ ] Feature X works correctly\n"
                "- [ ] Tests are added\n"
            )
            task_path = prov_dir / "TASK-pres1.md"
            task_path.write_text(original_content)

            create_task(task_id="pres1", file_path=str(task_path))

            new_path, action = review_reject_task(
                task_path,
                feedback="Tests don't cover edge cases.",
                rejected_by="gk-testing",
            )

            content = new_path.read_text()
            # Original content must still be present
            assert "# [TASK-pres1] Important feature" in content
            assert "## Context" in content
            assert "This task implements feature X" in content
            assert "## Acceptance Criteria" in content
            assert "Feature X works correctly" in content
            assert "Tests are added" in content
            # Rejection feedback must also be present
            assert "## Review Feedback" in content
            assert "Tests don't cover edge cases" in content

    def test_multiple_rejections_accumulate(self, mock_config, initialized_db):
        """Test that multiple rejections accumulate without losing earlier feedback."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task
            from orchestrator.queue_utils import review_reject_task, submit_completion

            # Create task and file in provisional
            prov_dir = mock_config / "shared" / "queue" / "provisional"
            prov_dir.mkdir(parents=True, exist_ok=True)
            incoming_dir = mock_config / "shared" / "queue" / "incoming"
            incoming_dir.mkdir(parents=True, exist_ok=True)

            original_content = (
                "# [TASK-multi1] Multi-reject test\n\n"
                "## Context\n"
                "Original context.\n\n"
                "## Acceptance Criteria\n"
                "- [ ] It works\n"
            )
            task_path = prov_dir / "TASK-multi1.md"
            task_path.write_text(original_content)

            create_task(task_id="multi1", file_path=str(task_path))

            # First rejection
            new_path, action = review_reject_task(
                task_path,
                feedback="First issue: boundary violation.",
                rejected_by="gk-arch",
            )
            assert action == "rejected"

            # Simulate re-claim and re-submit (move file back to provisional)
            resubmit_path = prov_dir / "TASK-multi1.md"
            new_path.rename(resubmit_path)
            from orchestrator.db import update_task
            update_task("multi1", file_path=str(resubmit_path))

            # Second rejection
            new_path2, action2 = review_reject_task(
                resubmit_path,
                feedback="Second issue: tests at wrong layer.",
                rejected_by="gk-testing",
            )
            assert action2 == "rejected"

            content = new_path2.read_text()
            # Original content preserved
            assert "# [TASK-multi1] Multi-reject test" in content
            assert "Original context." in content
            assert "## Acceptance Criteria" in content
            # Both rejection feedbacks present
            assert "First issue: boundary violation" in content
            assert "Second issue: tests at wrong layer" in content

    def test_header_remains_first_heading(self, mock_config, initialized_db):
        """Test that the # [TASK-xxx] header remains as the first heading after rejection."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task
            from orchestrator.queue_utils import review_reject_task

            prov_dir = mock_config / "shared" / "queue" / "provisional"
            prov_dir.mkdir(parents=True, exist_ok=True)

            original_content = "# [TASK-head1] Title stays first\n\n## Context\nSome context.\n"
            task_path = prov_dir / "TASK-head1.md"
            task_path.write_text(original_content)

            create_task(task_id="head1", file_path=str(task_path))

            new_path, _ = review_reject_task(
                task_path,
                feedback="Some feedback.",
                rejected_by="gk",
            )

            content = new_path.read_text()
            lines = content.strip().splitlines()
            # First non-empty line should be the task header
            first_heading = next(l for l in lines if l.startswith("#"))
            assert first_heading.startswith("# [TASK-head1]")


# =============================================================================
# File Path Tracking Tests
# =============================================================================


class TestFilePathTracking:
    """Tests that file_path in DB is updated when task files move between queues."""

    def test_submit_completion_updates_file_path(self, mock_config, initialized_db):
        """Test that submit_completion updates file_path in DB after moving file."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task
            from orchestrator.queue_utils import submit_completion

            incoming_dir = mock_config / "shared" / "queue" / "incoming"
            incoming_dir.mkdir(parents=True, exist_ok=True)
            task_path = incoming_dir / "TASK-fp1.md"
            task_path.write_text("# [TASK-fp1] Path tracking test\n")

            create_task(task_id="fp1", file_path=str(task_path))

            new_path = submit_completion(task_path, commits_count=2)

            task = get_task("fp1")
            assert task["file_path"] == str(new_path)
            assert "provisional" in task["file_path"]

    def test_accept_completion_updates_file_path(self, mock_config, initialized_db):
        """Test that accept_completion updates file_path in DB after moving file."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task
            from orchestrator.queue_utils import accept_completion

            prov_dir = mock_config / "shared" / "queue" / "provisional"
            prov_dir.mkdir(parents=True, exist_ok=True)
            task_path = prov_dir / "TASK-fp2.md"
            task_path.write_text("# [TASK-fp2] Path tracking test\n")

            create_task(task_id="fp2", file_path=str(task_path))

            new_path = accept_completion(task_path, accepted_by="pre_check")

            task = get_task("fp2")
            assert task["file_path"] == str(new_path)
            assert "done" in task["file_path"]

    def test_reject_completion_updates_file_path(self, mock_config, initialized_db):
        """Test that reject_completion updates file_path in DB after moving file."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task
            from orchestrator.queue_utils import reject_completion

            prov_dir = mock_config / "shared" / "queue" / "provisional"
            prov_dir.mkdir(parents=True, exist_ok=True)
            task_path = prov_dir / "TASK-fp3.md"
            task_path.write_text("# [TASK-fp3] Path tracking test\n")

            create_task(task_id="fp3", file_path=str(task_path))

            new_path = reject_completion(task_path, reason="no commits")

            task = get_task("fp3")
            assert task["file_path"] == str(new_path)
            assert "incoming" in task["file_path"]

    def test_review_reject_updates_file_path(self, mock_config, initialized_db):
        """Test that review_reject_task updates file_path in DB after moving file."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task
            from orchestrator.queue_utils import review_reject_task

            prov_dir = mock_config / "shared" / "queue" / "provisional"
            prov_dir.mkdir(parents=True, exist_ok=True)
            task_path = prov_dir / "TASK-fp4.md"
            task_path.write_text("# [TASK-fp4] Path tracking test\n")

            create_task(task_id="fp4", file_path=str(task_path))

            new_path, action = review_reject_task(
                task_path, "needs work", rejected_by="gk"
            )

            task = get_task("fp4")
            assert task["file_path"] == str(new_path)
            assert "incoming" in task["file_path"]

    def test_full_lifecycle_path_tracking(self, mock_config, initialized_db):
        """Test file_path tracking through submit → reject → re-submit → accept."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, get_task
            from orchestrator.queue_utils import (
                submit_completion, review_reject_task, accept_completion,
            )

            incoming_dir = mock_config / "shared" / "queue" / "incoming"
            incoming_dir.mkdir(parents=True, exist_ok=True)
            original_content = "# [TASK-lc1] Lifecycle test\n\n## Context\nOriginal.\n"
            task_path = incoming_dir / "TASK-lc1.md"
            task_path.write_text(original_content)

            create_task(task_id="lc1", file_path=str(task_path))

            # Step 1: Submit (incoming → provisional)
            prov_path = submit_completion(task_path, commits_count=1)
            task = get_task("lc1")
            assert "provisional" in task["file_path"]

            # Step 2: Reject (provisional → incoming)
            incoming_path, action = review_reject_task(
                prov_path, "needs fixes", rejected_by="gk"
            )
            task = get_task("lc1")
            assert "incoming" in task["file_path"]
            # Content still intact
            content = incoming_path.read_text()
            assert "# [TASK-lc1] Lifecycle test" in content
            assert "Original." in content
            assert "needs fixes" in content

            # Step 3: Re-submit (incoming → provisional)
            prov_path2 = submit_completion(incoming_path, commits_count=2)
            task = get_task("lc1")
            assert "provisional" in task["file_path"]

            # Step 4: Accept (provisional → done)
            done_path = accept_completion(prov_path2, accepted_by="v")
            task = get_task("lc1")
            assert "done" in task["file_path"]
            # All content preserved
            final_content = done_path.read_text()
            assert "# [TASK-lc1] Lifecycle test" in final_content
            assert "Original." in final_content
            assert "needs fixes" in final_content


# =============================================================================
# Get Review Feedback Tests (queue_utils.py)
# =============================================================================


class TestGetReviewFeedback:
    """Tests for get_review_feedback() in queue_utils.py."""

    def test_extracts_feedback_from_file(self, mock_config, initialized_db):
        """Test that get_review_feedback extracts feedback sections."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task
            from orchestrator.queue_utils import get_review_feedback

            incoming_dir = mock_config / "shared" / "queue" / "incoming"
            incoming_dir.mkdir(parents=True, exist_ok=True)
            task_path = incoming_dir / "TASK-fb1.md"
            task_path.write_text("""# [TASK-fb1] Test task

## Context
Some context.

## Review Feedback (rejection #1)

Fix the boundary violation in Engine.ts line 42.

REVIEW_REJECTED_AT: 2026-02-07
REVIEW_REJECTED_BY: gk-architecture

## Review Feedback (rejection #2)

Tests don't test the right layer.

REVIEW_REJECTED_AT: 2026-02-07
REVIEW_REJECTED_BY: gk-testing
""")

            create_task(task_id="fb1", file_path=str(task_path))

            feedback = get_review_feedback("fb1")

            assert feedback is not None
            assert "boundary violation" in feedback
            assert "right layer" in feedback

    def test_returns_none_when_no_feedback(self, mock_config, initialized_db):
        """Test that get_review_feedback returns None when no feedback exists."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task
            from orchestrator.queue_utils import get_review_feedback

            incoming_dir = mock_config / "shared" / "queue" / "incoming"
            incoming_dir.mkdir(parents=True, exist_ok=True)
            task_path = incoming_dir / "TASK-nofb.md"
            task_path.write_text("# [TASK-nofb] Clean task\n\n## Context\nNo feedback here.\n")

            create_task(task_id="nofb", file_path=str(task_path))

            feedback = get_review_feedback("nofb")
            assert feedback is None

    def test_returns_none_for_unknown_task(self, mock_config, initialized_db):
        """Test that get_review_feedback returns None for unknown task."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.queue_utils import get_review_feedback

            feedback = get_review_feedback("nonexistent")
            assert feedback is None


# =============================================================================
# Claim Task Prioritization Tests
# =============================================================================


class TestClaimTaskPrioritization:
    """Tests for claim_task() prioritizing rejected tasks."""

    def test_rejected_tasks_claimed_before_fresh(self, initialized_db):
        """Test that tasks with rejection_count > 0 are claimed before fresh tasks."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, claim_task

            # Create fresh P1 task
            create_task(task_id="fresh1", file_path="/fresh1.md", priority="P1")

            # Create rejected P1 task (same priority but has rejections)
            create_task(task_id="rejected1", file_path="/rejected1.md", priority="P1")
            update_task("rejected1", rejection_count=1)

            # The rejected task should be claimed first
            claimed = claim_task()
            assert claimed["id"] == "rejected1"

    def test_rejected_tasks_even_lower_priority_claimed_first(self, initialized_db):
        """Test that rejected P2 tasks are claimed before fresh P1 tasks."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, claim_task

            # Create fresh P1 task (higher priority)
            create_task(task_id="hp_fresh", file_path="/hp_fresh.md", priority="P1")

            # Create rejected P2 task (lower priority but rejected)
            create_task(task_id="lp_rejected", file_path="/lp_rejected.md", priority="P2")
            update_task("lp_rejected", rejection_count=2)

            # The rejected task should still be claimed first
            claimed = claim_task()
            assert claimed["id"] == "lp_rejected"


# =============================================================================
# Review Utils Tests
# =============================================================================


class TestReviewUtils:
    """Tests for review_utils.py module."""

    def test_init_task_review(self, mock_config):
        """Test initializing review tracking for a task."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import init_task_review, load_review_meta

            review_dir = init_task_review(
                "test1",
                branch="agent/test1",
                base_branch="main",
                required_checks=["architecture", "testing"],
            )

            assert review_dir.exists()
            assert (review_dir / "meta.json").exists()
            assert (review_dir / "checks" / "architecture.json").exists()
            assert (review_dir / "checks" / "testing.json").exists()

            meta = load_review_meta("test1")
            assert meta["status"] == "in_progress"
            assert meta["branch"] == "agent/test1"
            assert len(meta["required_checks"]) == 2

    def test_record_review_result(self, mock_config):
        """Test recording a single review check result."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import (
                init_task_review, record_review_result, load_check_result,
            )

            init_task_review("rec1", branch="agent/rec1")

            record_review_result(
                "rec1",
                "architecture",
                "pass",
                "All good",
                details="No issues found.",
                submitted_by="gk-arch",
            )

            result = load_check_result("rec1", "architecture")
            assert result["status"] == "pass"
            assert result["summary"] == "All good"
            assert result["submitted_by"] == "gk-arch"

    def test_all_reviews_complete_when_all_done(self, mock_config):
        """Test all_reviews_complete returns True when all checks are done."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import (
                init_task_review, record_review_result, all_reviews_complete,
            )

            init_task_review("comp1", branch="b", required_checks=["a", "b"])
            assert all_reviews_complete("comp1") is False

            record_review_result("comp1", "a", "pass", "ok")
            assert all_reviews_complete("comp1") is False

            record_review_result("comp1", "b", "fail", "bad")
            assert all_reviews_complete("comp1") is True

    def test_all_reviews_passed(self, mock_config):
        """Test all_reviews_passed correctly identifies failures."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import (
                init_task_review, record_review_result, all_reviews_passed,
            )

            init_task_review("pass1", branch="b", required_checks=["a", "b", "c"])
            record_review_result("pass1", "a", "pass", "ok")
            record_review_result("pass1", "b", "fail", "bad")
            record_review_result("pass1", "c", "pass", "ok")

            passed, failed = all_reviews_passed("pass1")
            assert passed is False
            assert failed == ["b"]

    def test_all_reviews_passed_when_all_pass(self, mock_config):
        """Test all_reviews_passed returns True when all pass."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import (
                init_task_review, record_review_result, all_reviews_passed,
            )

            init_task_review("allp1", branch="b", required_checks=["x", "y"])
            record_review_result("allp1", "x", "pass", "ok")
            record_review_result("allp1", "y", "pass", "ok")

            passed, failed = all_reviews_passed("allp1")
            assert passed is True
            assert failed == []

    def test_get_review_feedback_aggregates(self, mock_config):
        """Test get_review_feedback aggregates failed check details."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import (
                init_task_review, record_review_result,
                get_review_feedback as review_feedback,
            )

            init_task_review("agg1", branch="b", required_checks=["arch", "test"])
            record_review_result("agg1", "arch", "fail", "Boundary issue", details="Engine.ts line 42")
            record_review_result("agg1", "test", "pass", "Tests look good")

            feedback = review_feedback("agg1")
            assert "REJECTED" in feedback
            assert "Boundary issue" in feedback
            assert "Engine.ts line 42" in feedback
            assert "PASSED" in feedback

    def test_cleanup_review(self, mock_config):
        """Test cleanup_review removes the review directory."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import init_task_review, cleanup_review, get_review_dir

            init_task_review("clean1", branch="b")
            assert get_review_dir("clean1").exists()

            result = cleanup_review("clean1")
            assert result is True
            assert not get_review_dir("clean1").exists()

    def test_has_active_review(self, mock_config):
        """Test has_active_review checks for in-progress reviews."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import init_task_review, has_active_review

            assert has_active_review("norev") is False

            init_task_review("actrev", branch="b")
            assert has_active_review("actrev") is True

    def test_idempotent_init(self, mock_config):
        """Test that initializing review twice doesn't corrupt state."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import (
                init_task_review, record_review_result, load_check_result,
            )

            init_task_review("idem1", branch="b", required_checks=["a"])
            record_review_result("idem1", "a", "pass", "ok")

            # Re-init should overwrite (fresh review)
            init_task_review("idem1", branch="b", required_checks=["a"])
            result = load_check_result("idem1", "a")
            assert result["status"] == "pending"


# =============================================================================
# _db_task_to_file_format Tests
# =============================================================================


class TestDbTaskToFileFormat:
    """Tests for _db_task_to_file_format including new fields."""

    def test_includes_new_fields(self, mock_config, initialized_db):
        """Test that _db_task_to_file_format includes rejection_count, pr_number, pr_url."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task
            from orchestrator.queue_utils import _db_task_to_file_format

            # Create task file so path exists
            incoming_dir = mock_config / "shared" / "queue" / "incoming"
            incoming_dir.mkdir(parents=True, exist_ok=True)
            task_path = incoming_dir / "TASK-fmt1.md"
            task_path.write_text("# [TASK-fmt1] Test\n")

            create_task(task_id="fmt1", file_path=str(task_path))
            update_task("fmt1", rejection_count=2, pr_number=55, pr_url="https://example.com/pr/55")

            from orchestrator.db import get_task
            db_task = get_task("fmt1")
            formatted = _db_task_to_file_format(db_task)

            assert formatted["rejection_count"] == 2
            assert formatted["pr_number"] == 55
            assert formatted["pr_url"] == "https://example.com/pr/55"


# =============================================================================
# Approve and Merge Tests
# =============================================================================


class TestApproveAndMerge:
    """Tests for approve_and_merge() in queue_utils.py."""

    def test_approve_moves_to_done(self, mock_config, initialized_db):
        """Test that approve_and_merge moves task to done."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
                from orchestrator.db import create_task, get_task
                from orchestrator.queue_utils import approve_and_merge

                # Create task file
                prov_dir = mock_config / "shared" / "queue" / "provisional"
                prov_dir.mkdir(parents=True, exist_ok=True)
                done_dir = mock_config / "shared" / "queue" / "done"
                done_dir.mkdir(parents=True, exist_ok=True)
                task_path = prov_dir / "TASK-appr1.md"
                task_path.write_text("# [TASK-appr1] Test\n")

                create_task(task_id="appr1", file_path=str(task_path))

                result = approve_and_merge("appr1")

                assert result["task_id"] == "appr1"
                # Task should be in done queue now
                task = get_task("appr1")
                assert task["queue"] == "done"

    def test_approve_with_pr_number(self, mock_config, initialized_db):
        """Test approve_and_merge attempts gh pr merge when PR number exists."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stderr="")

                    from orchestrator.db import create_task, update_task
                    from orchestrator.queue_utils import approve_and_merge

                    prov_dir = mock_config / "shared" / "queue" / "provisional"
                    prov_dir.mkdir(parents=True, exist_ok=True)
                    done_dir = mock_config / "shared" / "queue" / "done"
                    done_dir.mkdir(parents=True, exist_ok=True)
                    task_path = prov_dir / "TASK-prmerge.md"
                    task_path.write_text("# [TASK-prmerge] Test\n")

                    create_task(task_id="prmerge", file_path=str(task_path))
                    update_task("prmerge", pr_number=42)

                    result = approve_and_merge("prmerge")

                    assert result["merged"] is True
                    # Verify gh pr merge was called
                    mock_run.assert_called_once()
                    call_args = mock_run.call_args
                    assert "gh" in call_args[0][0]
                    assert "42" in call_args[0][0]

    def test_approve_nonexistent_task(self, mock_config, initialized_db):
        """Test approve_and_merge with nonexistent task returns error."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
                from orchestrator.queue_utils import approve_and_merge

                result = approve_and_merge("doesnotexist")
                assert "error" in result

    def test_approve_with_stale_file_path(self, mock_config, initialized_db):
        """Test approve_and_merge works when DB file_path is stale.

        Reproduces the bug where file_path in DB still points to incoming/
        but the file has been moved to provisional/ by the scheduler.
        The DB queue field should still be updated to 'done'.
        """
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
                from orchestrator.db import create_task, get_task
                from orchestrator.queue_utils import approve_and_merge

                # Create directories
                incoming_dir = mock_config / "shared" / "queue" / "incoming"
                prov_dir = mock_config / "shared" / "queue" / "provisional"
                done_dir = mock_config / "shared" / "queue" / "done"
                incoming_dir.mkdir(parents=True, exist_ok=True)
                prov_dir.mkdir(parents=True, exist_ok=True)
                done_dir.mkdir(parents=True, exist_ok=True)

                # Create task with file_path pointing to incoming/ (stale)
                stale_path = incoming_dir / "TASK-stale1.md"
                create_task(task_id="stale1", file_path=str(stale_path))

                # But the actual file is in provisional/ (scheduler moved it)
                actual_path = prov_dir / "TASK-stale1.md"
                actual_path.write_text("# [TASK-stale1] Test stale path\n")
                # stale_path does NOT exist on disk

                result = approve_and_merge("stale1")

                assert result["task_id"] == "stale1"
                # DB should be updated to done despite stale path
                task = get_task("stale1")
                assert task["queue"] == "done"
                # File should have been found in provisional/ and moved to done/
                assert (done_dir / "TASK-stale1.md").exists()
                assert not actual_path.exists()

    def test_approve_with_stale_path_no_file(self, mock_config, initialized_db):
        """Test approve_and_merge works even when file is missing entirely.

        The DB queue should still be updated to 'done'.
        """
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
                from orchestrator.db import create_task, get_task
                from orchestrator.queue_utils import approve_and_merge

                done_dir = mock_config / "shared" / "queue" / "done"
                done_dir.mkdir(parents=True, exist_ok=True)

                # Create task with a file_path that doesn't exist
                create_task(task_id="ghost1", file_path="/nonexistent/TASK-ghost1.md")

                result = approve_and_merge("ghost1")

                assert result["task_id"] == "ghost1"
                # DB should still be updated to done
                task = get_task("ghost1")
                assert task["queue"] == "done"


# =============================================================================
# Gatekeeper Backpressure Tests
# =============================================================================


class TestGatekeeperBackpressure:
    """Tests for gatekeeper backpressure check."""

    def test_no_reviews_blocks(self, mock_config):
        """Test that no active reviews blocks gatekeeper spawn."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.backpressure import check_gatekeeper_backpressure

            can_proceed, reason = check_gatekeeper_backpressure()
            assert can_proceed is False

    def test_active_reviews_allows(self, mock_config):
        """Test that active reviews allow gatekeeper spawn."""
        with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
            from orchestrator.review_utils import init_task_review
            from orchestrator.backpressure import check_gatekeeper_backpressure

            init_task_review("bp1", branch="b")

            can_proceed, reason = check_gatekeeper_backpressure()
            assert can_proceed is True


# =============================================================================
# Process Gatekeeper Reviews Tests (Scheduler)
# =============================================================================


class TestProcessGatekeeperReviews:
    """Tests for process_gatekeeper_reviews() in scheduler.py."""

    def test_initializes_review_for_provisional_task(self, mock_config, initialized_db):
        """Test that process_gatekeeper_reviews initializes review for new provisional tasks."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
                with patch('orchestrator.config.is_gatekeeper_enabled', return_value=True):
                    with patch('orchestrator.config.get_gatekeeper_config', return_value={
                        'enabled': True,
                        'required_checks': ['architecture', 'testing'],
                        'max_rejections': 3,
                    }):
                        from orchestrator.db import create_task, update_task_queue
                        from orchestrator.scheduler import process_gatekeeper_reviews
                        from orchestrator.review_utils import has_active_review

                        # Create a provisional task with commits
                        create_task(task_id="gk1", file_path="/gk1.md", branch="agent/gk1")
                        update_task_queue("gk1", "provisional", commits_count=3)

                        process_gatekeeper_reviews()

                        # Review should be initialized
                        assert has_active_review("gk1") is True

    def test_accepts_task_when_all_checks_pass(self, mock_config, initialized_db):
        """Test that task is accepted when all gatekeeper checks pass."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
                with patch('orchestrator.config.is_gatekeeper_enabled', return_value=True):
                    with patch('orchestrator.config.get_gatekeeper_config', return_value={
                        'enabled': True,
                        'required_checks': ['architecture', 'testing'],
                        'max_rejections': 3,
                    }):
                        from orchestrator.db import create_task, update_task_queue, get_task
                        from orchestrator.review_utils import (
                            init_task_review, record_review_result,
                        )
                        from orchestrator.scheduler import process_gatekeeper_reviews

                        create_task(task_id="gkpass", file_path="/gkpass.md", branch="agent/gkpass")
                        update_task_queue("gkpass", "provisional", commits_count=2)

                        # Init review and mark all as passed
                        init_task_review("gkpass", branch="agent/gkpass", required_checks=["architecture", "testing"])
                        record_review_result("gkpass", "architecture", "pass", "All good")
                        record_review_result("gkpass", "testing", "pass", "Tests solid")

                        process_gatekeeper_reviews()

                        task = get_task("gkpass")
                        assert task["queue"] == "done"

    def test_rejects_task_when_check_fails(self, mock_config, initialized_db):
        """Test that task is rejected when a gatekeeper check fails."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
                with patch('orchestrator.config.is_gatekeeper_enabled', return_value=True):
                    with patch('orchestrator.config.get_gatekeeper_config', return_value={
                        'enabled': True,
                        'required_checks': ['architecture', 'testing'],
                        'max_rejections': 3,
                    }):
                        from orchestrator.db import create_task, update_task_queue, get_task
                        from orchestrator.review_utils import (
                            init_task_review, record_review_result,
                        )
                        from orchestrator.scheduler import process_gatekeeper_reviews

                        # Create task file so review_reject_task can write to it
                        prov_dir = mock_config / "shared" / "queue" / "provisional"
                        prov_dir.mkdir(parents=True, exist_ok=True)
                        incoming_dir = mock_config / "shared" / "queue" / "incoming"
                        incoming_dir.mkdir(parents=True, exist_ok=True)

                        task_path = prov_dir / "TASK-gkfail.md"
                        task_path.write_text("# [TASK-gkfail] Test\n")

                        create_task(task_id="gkfail", file_path=str(task_path), branch="agent/gkfail")
                        update_task_queue("gkfail", "provisional", commits_count=1)

                        # Init review, pass one, fail one
                        init_task_review("gkfail", branch="agent/gkfail", required_checks=["architecture", "testing"])
                        record_review_result("gkfail", "architecture", "pass", "ok")
                        record_review_result("gkfail", "testing", "fail", "Tests missing", details="No unit tests found")

                        process_gatekeeper_reviews()

                        task = get_task("gkfail")
                        assert task["queue"] == "incoming"
                        assert task["rejection_count"] == 1

    def test_skips_auto_accept_tasks(self, mock_config, initialized_db):
        """Test that auto_accept tasks are skipped by gatekeeper reviews."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
                with patch('orchestrator.config.is_gatekeeper_enabled', return_value=True):
                    with patch('orchestrator.config.get_gatekeeper_config', return_value={
                        'enabled': True,
                        'required_checks': ['architecture'],
                        'max_rejections': 3,
                    }):
                        from orchestrator.db import create_task, update_task_queue
                        from orchestrator.scheduler import process_gatekeeper_reviews
                        from orchestrator.review_utils import has_active_review

                        create_task(task_id="auto1", file_path="/auto1.md", auto_accept=True)
                        update_task_queue("auto1", "provisional", commits_count=1)

                        process_gatekeeper_reviews()

                        # Should NOT have initialized review
                        assert has_active_review("auto1") is False

    def test_skips_tasks_without_commits(self, mock_config, initialized_db):
        """Test that tasks without commits are skipped (pre-check handles them)."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
                with patch('orchestrator.config.is_gatekeeper_enabled', return_value=True):
                    with patch('orchestrator.config.get_gatekeeper_config', return_value={
                        'enabled': True,
                        'required_checks': ['architecture'],
                        'max_rejections': 3,
                    }):
                        from orchestrator.db import create_task, update_task_queue
                        from orchestrator.scheduler import process_gatekeeper_reviews
                        from orchestrator.review_utils import has_active_review

                        create_task(task_id="nocom1", file_path="/nocom1.md")
                        update_task_queue("nocom1", "provisional", commits_count=0)

                        process_gatekeeper_reviews()

                        assert has_active_review("nocom1") is False

    def test_disabled_gatekeeper_noop(self, mock_config, initialized_db):
        """Test that disabled gatekeeper system is a no-op."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.scheduler.is_gatekeeper_enabled', return_value=False):
                from orchestrator.db import create_task, update_task_queue
                from orchestrator.scheduler import process_gatekeeper_reviews
                from orchestrator.review_utils import has_active_review

                create_task(task_id="dis1", file_path="/dis1.md")
                update_task_queue("dis1", "provisional", commits_count=2)

                # Should be a no-op when disabled
                with patch('orchestrator.review_utils.get_orchestrator_dir', return_value=mock_config):
                    process_gatekeeper_reviews()
                    assert has_active_review("dis1") is False


# =============================================================================
# Scheduler Environment Variable Tests
# =============================================================================


class TestSchedulerGatekeeperEnv:
    """Tests for scheduler passing review env vars to gatekeepers."""

    def test_spawn_agent_passes_review_env(self, tmp_path):
        """Test that spawn_agent passes REVIEW_TASK_ID and REVIEW_CHECK_NAME."""
        fake_parent = tmp_path / "project"
        fake_parent.mkdir()
        (fake_parent / "orchestrator").mkdir()  # for PYTHONPATH
        fake_agents = tmp_path / "agents"
        fake_agents.mkdir()
        fake_orch = tmp_path / ".orchestrator"
        fake_orch.mkdir()

        with patch('orchestrator.scheduler.find_parent_project', return_value=fake_parent):
            with patch('orchestrator.scheduler.get_worktree_path', return_value=fake_parent):
                with patch('orchestrator.scheduler.get_agents_runtime_dir', return_value=fake_agents):
                    with patch('orchestrator.scheduler.get_port_env_vars', return_value={}):
                        with patch('orchestrator.scheduler.get_orchestrator_dir', return_value=fake_orch):
                            with patch('subprocess.Popen') as mock_popen:
                                mock_popen.return_value = MagicMock(pid=12345)

                                from orchestrator.scheduler import spawn_agent

                                config = {
                                    "role": "gatekeeper",
                                    "focus": "architecture",
                                    "review_task_id": "abc123",
                                    "review_check_name": "architecture",
                                }

                                pid = spawn_agent("gk-arch", 10, "gatekeeper", config)

                                # Verify env vars were set
                                call_kwargs = mock_popen.call_args
                                env = call_kwargs.kwargs.get("env", call_kwargs[1].get("env", {}))
                                assert env.get("REVIEW_TASK_ID") == "abc123"
                                assert env.get("REVIEW_CHECK_NAME") == "architecture"
                                assert env.get("AGENT_FOCUS") == "architecture"
