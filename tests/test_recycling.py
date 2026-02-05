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
        """Task with 0 commits + 50 turns is burned out."""
        from orchestrator.queue_utils import is_burned_out

        assert is_burned_out(commits_count=0, turns_used=50) is True

    def test_detect_burned_task_at_threshold(self):
        """Task with 0 commits + 40 turns is burned out (threshold)."""
        from orchestrator.queue_utils import is_burned_out

        assert is_burned_out(commits_count=0, turns_used=40) is True

    def test_detect_burned_task_low_turns_not_burned(self):
        """Task with 0 commits + 10 turns is NOT burned (early error, not too-large)."""
        from orchestrator.queue_utils import is_burned_out

        assert is_burned_out(commits_count=0, turns_used=10) is False

    def test_detect_normal_task_with_commits(self):
        """Task with 3 commits + 50 turns is normal."""
        from orchestrator.queue_utils import is_burned_out

        assert is_burned_out(commits_count=3, turns_used=50) is False


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

    def test_recycle_rewires_dependencies(self, mock_config, sample_project_with_tasks):
        """Tasks blocked by failed task get re-wired to breakdown task."""
        with patch('orchestrator.db.get_database_path', return_value=sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import recycle_to_breakdown
                    from orchestrator.db import get_task

                    burned = sample_project_with_tasks["burned_task"]
                    result = recycle_to_breakdown(burned["path"])

                    # The blocked task should now reference the new breakdown task
                    blocked_task = get_task("block001")
                    breakdown_task_id = result["breakdown_task_id"]

                    # blocked_by should have been updated from burn0001 to the breakdown task
                    assert blocked_task["blocked_by"] is not None
                    assert breakdown_task_id in blocked_task["blocked_by"]
                    assert "burn0001" not in blocked_task["blocked_by"]

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
