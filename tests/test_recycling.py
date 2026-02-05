"""Tests for task recycling to re-breakdown."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch


# =============================================================================
# Detection heuristic tests
# =============================================================================


class TestBurnedOutDetection:
    """Tests for the burned-out task detection heuristic."""

    def test_detect_burned_task(self):
        """Task with 0 commits + 100 turns is burned out."""
        from orchestrator.queue_utils import is_burned_out

        assert is_burned_out(commits_count=0, turns_used=100) is True

    def test_detect_burned_task_at_threshold(self):
        """Task with 0 commits + 80 turns is burned out (threshold)."""
        from orchestrator.queue_utils import is_burned_out

        assert is_burned_out(commits_count=0, turns_used=80) is True

    def test_detect_burned_task_low_turns_not_burned(self):
        """Task with 0 commits + 10 turns is NOT burned (early error, not too-large)."""
        from orchestrator.queue_utils import is_burned_out

        assert is_burned_out(commits_count=0, turns_used=10) is False

    def test_detect_burned_task_below_threshold_not_burned(self):
        """Task with 0 commits + 50 turns is NOT burned (below threshold)."""
        from orchestrator.queue_utils import is_burned_out

        assert is_burned_out(commits_count=0, turns_used=50) is False

    def test_detect_normal_task_with_commits(self):
        """Task with 3 commits + 100 turns is normal."""
        from orchestrator.queue_utils import is_burned_out

        assert is_burned_out(commits_count=3, turns_used=100) is False


# =============================================================================
# recycle_to_breakdown() tests
# =============================================================================


class TestRecycleToBreakdown:
    """Tests for recycle_to_breakdown function."""

    def test_recycle_creates_breakdown_task(self, mock_config, sample_project_with_tasks):
        """Recycling creates a new task in the breakdown queue."""
        with patch('orchestrator.db.get_database_path', return_value=sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import recycle_to_breakdown

                    burned = sample_project_with_tasks["burned_task"]
                    result = recycle_to_breakdown(burned["path"])

                    assert result is not None
                    assert "breakdown_task" in result

                    # Verify breakdown task file exists
                    breakdown_dir = mock_config / "shared" / "queue" / "breakdown"
                    breakdown_files = list(breakdown_dir.glob("TASK-*.md"))
                    assert len(breakdown_files) >= 1

    def test_recycle_includes_project_context(self, mock_config, sample_project_with_tasks):
        """Breakdown task content includes project title and branch."""
        with patch('orchestrator.db.get_database_path', return_value=sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import recycle_to_breakdown

                    burned = sample_project_with_tasks["burned_task"]
                    result = recycle_to_breakdown(burned["path"])

                    # Read the breakdown task content
                    breakdown_dir = mock_config / "shared" / "queue" / "breakdown"
                    breakdown_files = list(breakdown_dir.glob("TASK-*.md"))
                    content = breakdown_files[0].read_text()

                    assert "PROJ-test1" in content
                    assert "feature/test1" in content
                    assert "Test project for recycling" in content

    def test_recycle_includes_completed_siblings(self, mock_config, sample_project_with_tasks):
        """Breakdown task lists completed sibling tasks with commit counts."""
        with patch('orchestrator.db.get_database_path', return_value=sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import recycle_to_breakdown

                    burned = sample_project_with_tasks["burned_task"]
                    result = recycle_to_breakdown(burned["path"])

                    breakdown_dir = mock_config / "shared" / "queue" / "breakdown"
                    breakdown_files = list(breakdown_dir.glob("TASK-*.md"))
                    content = breakdown_files[0].read_text()

                    # Should mention completed tasks
                    assert "done0001" in content
                    assert "done0002" in content
                    assert "done0003" in content
                    # Should show commit counts
                    assert "1 commit" in content
                    assert "2 commit" in content

    def test_recycle_includes_failed_task_content(self, mock_config, sample_project_with_tasks):
        """Original failed task description embedded in full."""
        with patch('orchestrator.db.get_database_path', return_value=sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import recycle_to_breakdown

                    burned = sample_project_with_tasks["burned_task"]
                    result = recycle_to_breakdown(burned["path"])

                    breakdown_dir = mock_config / "shared" / "queue" / "breakdown"
                    breakdown_files = list(breakdown_dir.glob("TASK-*.md"))
                    content = breakdown_files[0].read_text()

                    # Should include the failed task's description
                    assert "Run the tests, debug failures, add edge case coverage" in content
                    assert "burn0001" in content

    def test_recycle_moves_original_to_recycled(self, mock_config, sample_project_with_tasks):
        """Original task moves to recycled queue state."""
        with patch('orchestrator.db.get_database_path', return_value=sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import recycle_to_breakdown
                    from orchestrator.db import get_task

                    burned = sample_project_with_tasks["burned_task"]
                    result = recycle_to_breakdown(burned["path"])

                    # Original task should be in recycled state in DB
                    task = get_task("burn0001")
                    assert task["queue"] == "recycled"

                    # Original file should have moved to recycled dir
                    recycled_dir = mock_config / "shared" / "queue" / "recycled"
                    assert (recycled_dir / "TASK-burn0001.md").exists()

    def test_recycle_preserves_original_dependencies(self, mock_config, sample_project_with_tasks):
        """Tasks blocked by recycled task stay blocked by original (rewired at approve time)."""
        with patch('orchestrator.db.get_database_path', return_value=sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import recycle_to_breakdown
                    from orchestrator.db import get_task

                    burned = sample_project_with_tasks["burned_task"]
                    result = recycle_to_breakdown(burned["path"])

                    # The blocked task should STILL reference the original task
                    # (rewiring happens later in approve_breakdown, not at recycle time)
                    blocked_task = get_task("block001")
                    assert blocked_task["blocked_by"] is not None
                    assert "burn0001" in blocked_task["blocked_by"]

    def test_recycle_non_project_task(self, mock_config, initialized_db):
        """Task without project_id still recycled but with less context."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import recycle_to_breakdown
                    from orchestrator.db import create_task, update_task

                    # Create a standalone burned task (no project)
                    prov_dir = mock_config / "shared" / "queue" / "provisional"
                    prov_dir.mkdir(parents=True, exist_ok=True)
                    task_path = prov_dir / "TASK-standalone.md"
                    task_path.write_text(
                        "# [TASK-standalone] Standalone burned task\n\n"
                        "ROLE: implement\nPRIORITY: P1\nBRANCH: main\n\n"
                        "## Context\nSome standalone work.\n\n"
                        "## Acceptance Criteria\n- [ ] Done\n"
                    )
                    create_task(task_id="standalone", file_path=str(task_path), role="implement")
                    update_task("standalone", queue="provisional", commits_count=0, turns_used=50)

                    result = recycle_to_breakdown(task_path)

                    assert result is not None
                    # Should still create a breakdown task
                    breakdown_dir = mock_config / "shared" / "queue" / "breakdown"
                    breakdown_files = list(breakdown_dir.glob("TASK-*.md"))
                    assert len(breakdown_files) >= 1

                    # Content should include the task but no project siblings
                    content = breakdown_files[0].read_text()
                    assert "standalone" in content.lower() or "Standalone burned task" in content


# =============================================================================
# Depth cap tests
# =============================================================================


class TestRecycleDepthCap:
    """Tests for re-breakdown depth limiting."""

    def test_recycle_depth_cap(self, mock_config, initialized_db):
        """Task with re_breakdown_depth >= 1 is NOT recycled."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import recycle_to_breakdown
                    from orchestrator.db import create_task, update_task

                    prov_dir = mock_config / "shared" / "queue" / "provisional"
                    prov_dir.mkdir(parents=True, exist_ok=True)
                    task_path = prov_dir / "TASK-deep0001.md"
                    task_path.write_text(
                        "# [TASK-deep0001] Already re-broken-down task\n\n"
                        "ROLE: implement\nPRIORITY: P1\nBRANCH: main\n"
                        "RE_BREAKDOWN_DEPTH: 1\n\n"
                        "## Context\nThis was already re-broken-down once.\n\n"
                        "## Acceptance Criteria\n- [ ] Done\n"
                    )
                    create_task(task_id="deep0001", file_path=str(task_path), role="implement")
                    update_task("deep0001", queue="provisional", commits_count=0, turns_used=50)

                    result = recycle_to_breakdown(task_path)

                    # Should return None or indicate escalation, not create a breakdown task
                    assert result is None or result.get("action") == "escalate_to_human"

    def test_recycle_sets_depth_on_new_tasks(self, mock_config, sample_project_with_tasks):
        """New breakdown task has re_breakdown_depth context for child tasks."""
        with patch('orchestrator.db.get_database_path', return_value=sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import recycle_to_breakdown

                    burned = sample_project_with_tasks["burned_task"]
                    result = recycle_to_breakdown(burned["path"])

                    # The breakdown task content should indicate depth
                    breakdown_dir = mock_config / "shared" / "queue" / "breakdown"
                    breakdown_files = list(breakdown_dir.glob("TASK-*.md"))
                    content = breakdown_files[0].read_text()

                    assert "RE_BREAKDOWN_DEPTH: 1" in content


# =============================================================================
# Fractal dependency rewiring (approve_breakdown)
# =============================================================================


class TestFractalDependencyRewiring:
    """Tests that approve_breakdown rewires external deps to leaf subtasks."""

    def _db_path(self, sample_project_with_tasks):
        return sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"

    def _patches(self, mock_config, db_path):
        """Return stacked context manager with all needed patches."""
        breakdowns_dir = mock_config / "shared" / "breakdowns"
        breakdowns_dir.mkdir(parents=True, exist_ok=True)
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch('orchestrator.db.get_database_path', return_value=db_path))
        stack.enter_context(patch('orchestrator.queue_utils.is_db_enabled', return_value=True))
        stack.enter_context(patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"))
        stack.enter_context(patch('orchestrator.queue_utils._create_and_push_branch', return_value=True))
        stack.enter_context(patch('orchestrator.queue_utils.get_breakdowns_dir', return_value=breakdowns_dir))
        return stack, breakdowns_dir

    def test_approve_rewires_to_leaf_subtasks(self, mock_config, sample_project_with_tasks):
        """When a re-breakdown is approved, external tasks blocked by the
        original recycled task get rewired to depend on leaf subtasks."""
        db_path = self._db_path(sample_project_with_tasks)
        stack, breakdowns_dir = self._patches(mock_config, db_path)
        with stack:
            from orchestrator.queue_utils import recycle_to_breakdown, approve_breakdown
            from orchestrator.db import get_task

            burned = sample_project_with_tasks["burned_task"]

            # Step 1: Recycle the burned task
            recycle_result = recycle_to_breakdown(burned["path"])
            assert recycle_result is not None

            # Verify external task still blocked by original
            blocked = get_task("block001")
            assert "burn0001" in blocked["blocked_by"]

            # Step 2: Create a breakdown file (simulating breakdown agent output)
            breakdown_file = breakdowns_dir / "test-rebreakdown.md"
            breakdown_file.write_text(
                "# Breakdown: Re-breakdown: burn0001\n\n"
                "**Branch:** feature/test1\n"
                "**Status:** pending_review\n\n"
                "## Task 1: First subtask\n\n"
                "**Role:** implement\n**Priority:** P1\n"
                "**Depends on:** (none)\n\n"
                "### Context\nDo step 1.\n\n"
                "### Acceptance Criteria\n- [ ] Step 1 done\n\n"
                "## Task 2: Second subtask\n\n"
                "**Role:** implement\n**Priority:** P1\n"
                "**Depends on:** 1\n\n"
                "### Context\nDo step 2.\n\n"
                "### Acceptance Criteria\n- [ ] Step 2 done\n\n"
                "## Task 3: Third subtask\n\n"
                "**Role:** implement\n**Priority:** P1\n"
                "**Depends on:** 1\n\n"
                "### Context\nDo step 3.\n\n"
                "### Acceptance Criteria\n- [ ] Step 3 done\n"
            )

            # Step 3: Approve the breakdown
            result = approve_breakdown("test-rebreakdown")

            assert result["tasks_created"] == 3
            leaf_ids = result["leaf_ids"]
            # Tasks 2 and 3 both depend on 1, so they are leaves
            assert len(leaf_ids) == 2

            # Step 4: Verify external task now blocked by leaf subtasks
            blocked = get_task("block001")
            blocked_by = blocked["blocked_by"]
            assert blocked_by is not None
            for lid in leaf_ids:
                assert lid in blocked_by, f"Leaf {lid} not in blocked_by: {blocked_by}"
            assert "burn0001" not in blocked_by

    def test_approve_non_rebreakdown_skips_rewiring(self, mock_config, initialized_db):
        """Normal breakdown (not a re-breakdown) doesn't do any rewiring."""
        breakdowns_dir = mock_config / "shared" / "breakdowns"
        breakdowns_dir.mkdir(parents=True, exist_ok=True)
        with patch('orchestrator.db.get_database_path', return_value=initialized_db), \
             patch('orchestrator.queue_utils.is_db_enabled', return_value=True), \
             patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"), \
             patch('orchestrator.queue_utils._create_and_push_branch', return_value=True), \
             patch('orchestrator.queue_utils.get_breakdowns_dir', return_value=breakdowns_dir):
            from orchestrator.queue_utils import approve_breakdown

            breakdown_file = breakdowns_dir / "normal-breakdown.md"
            breakdown_file.write_text(
                "# Breakdown: New feature\n\n"
                "**Branch:** feature/new-thing\n"
                "**Status:** pending_review\n\n"
                "## Task 1: First task\n\n"
                "**Role:** implement\n**Priority:** P1\n"
                "**Depends on:** (none)\n\n"
                "### Context\nDo the thing.\n\n"
                "### Acceptance Criteria\n- [ ] Done\n\n"
                "## Task 2: Second task\n\n"
                "**Role:** implement\n**Priority:** P1\n"
                "**Depends on:** 1\n\n"
                "### Context\nDo more.\n\n"
                "### Acceptance Criteria\n- [ ] Done\n"
            )

            result = approve_breakdown("normal-breakdown")
            assert result["tasks_created"] == 2

    def test_approve_linear_chain_rewires_to_last(self, mock_config, sample_project_with_tasks):
        """Linear chain A->B->C: leaf is C only, external deps rewired to C."""
        db_path = self._db_path(sample_project_with_tasks)
        stack, breakdowns_dir = self._patches(mock_config, db_path)
        with stack:
            from orchestrator.queue_utils import recycle_to_breakdown, approve_breakdown
            from orchestrator.db import get_task

            burned = sample_project_with_tasks["burned_task"]
            recycle_to_breakdown(burned["path"])

            breakdown_file = breakdowns_dir / "linear-chain.md"
            breakdown_file.write_text(
                "# Breakdown: Re-breakdown: burn0001\n\n"
                "**Branch:** feature/test1\n"
                "**Status:** pending_review\n\n"
                "## Task 1: Step A\n\n"
                "**Role:** implement\n**Priority:** P1\n"
                "**Depends on:** (none)\n\n"
                "### Context\nStep A.\n\n"
                "### Acceptance Criteria\n- [ ] A done\n\n"
                "## Task 2: Step B\n\n"
                "**Role:** implement\n**Priority:** P1\n"
                "**Depends on:** 1\n\n"
                "### Context\nStep B.\n\n"
                "### Acceptance Criteria\n- [ ] B done\n\n"
                "## Task 3: Step C\n\n"
                "**Role:** implement\n**Priority:** P1\n"
                "**Depends on:** 2\n\n"
                "### Context\nStep C.\n\n"
                "### Acceptance Criteria\n- [ ] C done\n"
            )

            result = approve_breakdown("linear-chain")
            leaf_ids = result["leaf_ids"]
            # Only task 3 is a leaf (1->2->3)
            assert len(leaf_ids) == 1

            # External task should now depend on task 3 only
            blocked = get_task("block001")
            assert leaf_ids[0] in blocked["blocked_by"]
            assert "burn0001" not in blocked["blocked_by"]
