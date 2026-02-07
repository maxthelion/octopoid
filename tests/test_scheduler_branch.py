"""Tests for scheduler worktree branch peeking."""

import pytest
from pathlib import Path
from unittest.mock import patch


class TestPeekTaskBranch:
    """Tests for peek_task_branch function."""

    def test_peek_breakdown_queue_returns_branch(self, mock_config, sample_project_with_tasks):
        """Breakdown agent gets task branch from breakdown queue."""
        db_path = sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"
        with patch('orchestrator.db.get_database_path', return_value=db_path):
            with patch('orchestrator.scheduler.is_db_enabled', return_value=True):
                from orchestrator.queue_utils import create_task
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        # Create a breakdown task with a feature branch
                        breakdown_dir = mock_config / "shared" / "queue" / "breakdown"
                        breakdown_dir.mkdir(parents=True, exist_ok=True)

                        task_path = create_task(
                            title="Re-breakdown test",
                            role="breakdown",
                            context="Test context",
                            acceptance_criteria=["Test"],
                            branch="feature/test1",
                            project_id="PROJ-test1",
                            queue="breakdown",
                        )

                        from orchestrator.scheduler import peek_task_branch
                        branch = peek_task_branch("breakdown")
                        assert branch == "feature/test1"

    def test_peek_returns_none_for_empty_queue(self, mock_config, initialized_db):
        """Returns None when queue is empty."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.scheduler.is_db_enabled', return_value=True):
                from orchestrator.scheduler import peek_task_branch
                branch = peek_task_branch("breakdown")
                assert branch is None

    def test_peek_returns_none_for_main_branch(self, mock_config, initialized_db):
        """Returns None when task is on main (no override needed)."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.scheduler.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        from orchestrator.queue_utils import create_task

                        breakdown_dir = mock_config / "shared" / "queue" / "breakdown"
                        breakdown_dir.mkdir(parents=True, exist_ok=True)

                        create_task(
                            title="Main branch task",
                            role="breakdown",
                            context="Test context",
                            acceptance_criteria=["Test"],
                            branch="main",
                            queue="breakdown",
                        )

                        from orchestrator.scheduler import peek_task_branch
                        branch = peek_task_branch("breakdown")
                        assert branch is None  # No override for main

    def test_peek_returns_none_for_unknown_role(self, mock_config, initialized_db):
        """Returns None for roles without queue mapping."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.scheduler.is_db_enabled', return_value=True):
                from orchestrator.scheduler import peek_task_branch
                branch = peek_task_branch("unknown_role")
                assert branch is None

    def test_peek_returns_none_without_db(self, mock_config):
        """Returns None when DB is not enabled."""
        with patch('orchestrator.scheduler.is_db_enabled', return_value=False):
            from orchestrator.scheduler import peek_task_branch
            branch = peek_task_branch("breakdown")
            assert branch is None

    def test_peek_returns_none_for_orchestrator_impl(self, mock_config, initialized_db):
        """orchestrator_impl always uses main — never overrides branch.

        The orchestrator_impl agent works inside the orchestrator/ submodule
        within a Boxen worktree, so the worktree must always be on main
        regardless of what branch the task file says.
        """
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.scheduler.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        from orchestrator.queue_utils import create_task

                        # Create an orchestrator_impl task with a non-main branch
                        create_task(
                            title="Fix scheduler bug",
                            role="orchestrator_impl",
                            context="Fix a bug in the scheduler",
                            acceptance_criteria=["Bug is fixed"],
                            branch="main",
                        )

                        from orchestrator.scheduler import peek_task_branch
                        branch = peek_task_branch("orchestrator_impl")
                        # Must return None (use main) even though the task has branch=main
                        assert branch is None
