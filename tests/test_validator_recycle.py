"""Tests for validator burned-out task recycling."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestValidatorBurnedOutRouting:
    """Tests for validator routing burned-out tasks to recycling."""

    def test_validator_recycles_burned_task(self, mock_config, sample_project_with_tasks):
        """Validator recycles a task with 0 commits and 50 turns."""
        db_path = sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"
        with patch('orchestrator.db.get_database_path', return_value=db_path):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    with patch('orchestrator.config.is_db_enabled', return_value=True):
                        from orchestrator.roles.validator import ValidatorRole
                        from orchestrator.db import get_task

                        validator = ValidatorRole.__new__(ValidatorRole)
                        validator.agent_name = "test-validator"
                        validator.log = MagicMock()
                        validator.debug_log = MagicMock()

                        burned = sample_project_with_tasks["burned_task"]

                        # Call _recycle_task directly
                        validator._recycle_task(burned["id"], burned["path"], 50)

                        # Verify it was recycled, not left in provisional
                        task = get_task(burned["id"])
                        assert task["queue"] == "recycled"

                        # Verify log was called with recycling message
                        validator.log.assert_any_call(
                            f"Recycling {burned['id']}: burned out (0 commits, 50 turns)"
                        )

    def test_validator_accepts_task_with_commits(self, mock_config, sample_project_with_tasks):
        """Validator accepts tasks that have commits even with max turns."""
        db_path = sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"
        with patch('orchestrator.db.get_database_path', return_value=db_path):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import is_burned_out

                    # A task with commits is not burned out regardless of turns
                    assert is_burned_out(commits_count=2, turns_used=50) is False
                    assert is_burned_out(commits_count=1, turns_used=40) is False

    def test_validator_cumulative_recycle(self, mock_config, sample_project_with_tasks):
        """Task with 3+ failed attempts gets recycled (cumulative catch)."""
        db_path = sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"
        with patch('orchestrator.db.get_database_path', return_value=db_path):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    with patch('orchestrator.config.is_db_enabled', return_value=True):
                        from orchestrator.roles.validator import ValidatorRole
                        from orchestrator.db import get_task

                        validator = ValidatorRole.__new__(ValidatorRole)
                        validator.agent_name = "test-validator"
                        validator.log = MagicMock()
                        validator.debug_log = MagicMock()

                        burned = sample_project_with_tasks["burned_task"]

                        # Use _recycle_or_escalate_task for cumulative failures
                        validator._recycle_or_escalate_task(burned["id"], burned["path"], 3)

                        # Should be recycled
                        task = get_task(burned["id"])
                        assert task["queue"] == "recycled"

    def test_validator_depth_cap_accepts(self, mock_config, initialized_db):
        """Task at depth cap gets accepted (for human review) instead of recycled."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    with patch('orchestrator.config.is_db_enabled', return_value=True):
                        from orchestrator.roles.validator import ValidatorRole
                        from orchestrator.db import create_task, update_task, get_task

                        # Create a task with RE_BREAKDOWN_DEPTH: 1
                        prov_dir = mock_config / "shared" / "queue" / "provisional"
                        prov_dir.mkdir(parents=True, exist_ok=True)
                        task_path = prov_dir / "TASK-deep0002.md"
                        task_path.write_text(
                            "# [TASK-deep0002] Depth-capped task\n\n"
                            "ROLE: implement\nPRIORITY: P1\nBRANCH: main\n"
                            "RE_BREAKDOWN_DEPTH: 1\n\n"
                            "## Context\nAlready re-broken-down once.\n\n"
                            "## Acceptance Criteria\n- [ ] Done\n"
                        )
                        create_task(task_id="deep0002", file_path=str(task_path), role="implement")
                        update_task("deep0002", queue="provisional", commits_count=0, turns_used=50)

                        validator = ValidatorRole.__new__(ValidatorRole)
                        validator.agent_name = "test-validator"
                        validator.log = MagicMock()
                        validator.debug_log = MagicMock()

                        # Recycle should return None (depth cap), then validator accepts
                        validator._recycle_task("deep0002", task_path, 50)

                        task = get_task("deep0002")
                        assert task["queue"] == "done"
