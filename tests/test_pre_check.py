"""Tests for orchestrator.roles.pre_check module."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import os


class TestPreCheckRole:
    """Tests for PreCheckRole class."""

    def test_pre_check_requires_db_mode(self, mock_config):
        """Test that pre-check returns early if DB not enabled."""
        with patch('orchestrator.roles.pre_check.is_db_enabled', return_value=False):
            with patch.dict(os.environ, {
                'AGENT_NAME': 'test-pre-check',
                'AGENT_ID': '0',
                'AGENT_ROLE': 'pre_check',
                'PARENT_PROJECT': str(mock_config.parent),
                'WORKTREE': str(mock_config.parent),
                'SHARED_DIR': str(mock_config / 'shared'),
                'ORCHESTRATOR_DIR': str(mock_config),
            }):
                from orchestrator.roles.pre_check import PreCheckRole

                role = PreCheckRole()
                result = role.run()

                assert result == 0  # Exits cleanly

    def test_pre_check_accepts_task_with_commits(self, mock_config, initialized_db):
        """Test that pre-check accepts tasks with commits."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.roles.pre_check.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        with patch('orchestrator.roles.pre_check.get_pre_check_config', return_value={
                            'require_commits': True,
                            'max_attempts_before_planning': 3,
                            'claim_timeout_minutes': 60,
                        }):
                            with patch.dict(os.environ, {
                                'AGENT_NAME': 'test-pre-check',
                                'AGENT_ID': '0',
                                'AGENT_ROLE': 'pre_check',
                                'PARENT_PROJECT': str(mock_config.parent),
                                'WORKTREE': str(mock_config.parent),
                                'SHARED_DIR': str(mock_config / 'shared'),
                                'ORCHESTRATOR_DIR': str(mock_config),
                            }):
                                from orchestrator.roles.pre_check import PreCheckRole
                                from orchestrator.db import create_task, claim_task, submit_completion, get_task

                                # Setup: create and submit a task with commits
                                create_task(task_id="valid1", file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-valid1.md"))
                                claim_task()
                                submit_completion("valid1", commits_count=3, turns_used=20)

                                # Create the task file
                                prov_dir = mock_config / "shared" / "queue" / "provisional"
                                prov_dir.mkdir(parents=True, exist_ok=True)
                                (prov_dir / "TASK-valid1.md").write_text("# [TASK-valid1] Test\n")

                                # Run pre-check
                                role = PreCheckRole()
                                result = role.run()

                                assert result == 0

                                # Task should be accepted (in done queue)
                                task = get_task("valid1")
                                assert task["queue"] == "done"

    def test_pre_check_rejects_task_without_commits(self, mock_config, initialized_db):
        """Test that pre-check rejects tasks without commits."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.roles.pre_check.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        with patch('orchestrator.roles.pre_check.get_pre_check_config', return_value={
                            'require_commits': True,
                            'max_attempts_before_planning': 3,
                            'claim_timeout_minutes': 60,
                        }):
                            with patch.dict(os.environ, {
                                'AGENT_NAME': 'test-pre-check',
                                'AGENT_ID': '0',
                                'AGENT_ROLE': 'pre_check',
                                'PARENT_PROJECT': str(mock_config.parent),
                                'WORKTREE': str(mock_config.parent),
                                'SHARED_DIR': str(mock_config / 'shared'),
                                'ORCHESTRATOR_DIR': str(mock_config),
                            }):
                                from orchestrator.roles.pre_check import PreCheckRole
                                from orchestrator.db import create_task, claim_task, submit_completion, get_task

                                # Setup: create and submit a task with NO commits
                                create_task(task_id="nocommit", file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-nocommit.md"))
                                claim_task()
                                submit_completion("nocommit", commits_count=0, turns_used=20)

                                # Create the task file
                                prov_dir = mock_config / "shared" / "queue" / "provisional"
                                prov_dir.mkdir(parents=True, exist_ok=True)
                                (prov_dir / "TASK-nocommit.md").write_text("# [TASK-nocommit] Test\n")

                                # Run pre-check
                                role = PreCheckRole()
                                result = role.run()

                                assert result == 0

                                # Task should be rejected (back in incoming)
                                task = get_task("nocommit")
                                assert task["queue"] == "incoming"
                                assert task["attempt_count"] == 1

    def test_pre_check_escalates_after_max_attempts(self, mock_config, initialized_db):
        """Test that pre-check escalates tasks after max attempts."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.roles.pre_check.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.planning.is_db_enabled', return_value=True):
                        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                            with patch('orchestrator.planning.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                                with patch('orchestrator.roles.pre_check.get_pre_check_config', return_value={
                                    'require_commits': True,
                                    'max_attempts_before_planning': 2,  # Low threshold for test
                                    'claim_timeout_minutes': 60,
                                }):
                                    with patch.dict(os.environ, {
                                        'AGENT_NAME': 'test-pre-check',
                                        'AGENT_ID': '0',
                                        'AGENT_ROLE': 'pre_check',
                                        'PARENT_PROJECT': str(mock_config.parent),
                                        'WORKTREE': str(mock_config.parent),
                                        'SHARED_DIR': str(mock_config / 'shared'),
                                        'ORCHESTRATOR_DIR': str(mock_config),
                                    }):
                                        from orchestrator.roles.pre_check import PreCheckRole
                                        from orchestrator.db import create_task, claim_task, submit_completion, get_task, update_task

                                        # Setup: create a task that has already failed twice
                                        create_task(
                                            task_id="escalate",
                                            file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-escalate.md")
                                        )
                                        update_task("escalate", attempt_count=2)  # Already failed twice
                                        claim_task()
                                        submit_completion("escalate", commits_count=0)

                                        # Create the task file
                                        prov_dir = mock_config / "shared" / "queue" / "provisional"
                                        prov_dir.mkdir(parents=True, exist_ok=True)
                                        task_file = prov_dir / "TASK-escalate.md"
                                        task_file.write_text("# [TASK-escalate] Test\nROLE: implement\n\n## Context\nTest task\n\n## Acceptance Criteria\n- [ ] Done\n")

                                        # Run pre-check
                                        role = PreCheckRole()
                                        result = role.run()

                                        assert result == 0

                                        # Task should be recycled (new behavior: recycle before escalate)
                                        task = get_task("escalate")
                                        assert task["queue"] == "recycled"

    def test_pre_check_accepts_without_commit_requirement(self, mock_config, initialized_db):
        """Test that pre-check accepts tasks when require_commits is False."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.roles.pre_check.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        with patch('orchestrator.roles.pre_check.get_pre_check_config', return_value={
                            'require_commits': False,  # Don't require commits
                            'max_attempts_before_planning': 3,
                            'claim_timeout_minutes': 60,
                        }):
                            with patch.dict(os.environ, {
                                'AGENT_NAME': 'test-pre-check',
                                'AGENT_ID': '0',
                                'AGENT_ROLE': 'pre_check',
                                'PARENT_PROJECT': str(mock_config.parent),
                                'WORKTREE': str(mock_config.parent),
                                'SHARED_DIR': str(mock_config / 'shared'),
                                'ORCHESTRATOR_DIR': str(mock_config),
                            }):
                                from orchestrator.roles.pre_check import PreCheckRole
                                from orchestrator.db import create_task, claim_task, submit_completion, get_task

                                # Setup: create and submit a task with NO commits
                                create_task(task_id="nocheck", file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-nocheck.md"))
                                claim_task()
                                submit_completion("nocheck", commits_count=0)

                                # Create the task file
                                prov_dir = mock_config / "shared" / "queue" / "provisional"
                                prov_dir.mkdir(parents=True, exist_ok=True)
                                (prov_dir / "TASK-nocheck.md").write_text("# [TASK-nocheck] Test\n")

                                # Run pre-check
                                role = PreCheckRole()
                                result = role.run()

                                assert result == 0

                                # Task should be accepted even without commits
                                task = get_task("nocheck")
                                assert task["queue"] == "done"


class TestPreCheckHelpers:
    """Tests for pre-check helper methods."""

    def test_reset_stuck_claimed(self, mock_config, initialized_db):
        """Test resetting stuck claimed tasks via pre-check."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.roles.pre_check.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        with patch('orchestrator.roles.pre_check.get_pre_check_config', return_value={
                            'require_commits': True,
                            'max_attempts_before_planning': 3,
                            'claim_timeout_minutes': 60,
                        }):
                            with patch.dict(os.environ, {
                                'AGENT_NAME': 'test-pre-check',
                                'AGENT_ID': '0',
                                'AGENT_ROLE': 'pre_check',
                                'PARENT_PROJECT': str(mock_config.parent),
                                'WORKTREE': str(mock_config.parent),
                                'SHARED_DIR': str(mock_config / 'shared'),
                                'ORCHESTRATOR_DIR': str(mock_config),
                            }):
                                from orchestrator.roles.pre_check import PreCheckRole
                                from orchestrator.db import create_task, get_connection, get_task

                                # Create a stuck claimed task
                                create_task(task_id="stuck", file_path="/stuck.md")
                                with get_connection() as conn:
                                    conn.execute("""
                                        UPDATE tasks
                                        SET queue = 'claimed',
                                            claimed_by = 'dead-agent',
                                            claimed_at = datetime('now', '-2 hours')
                                        WHERE id = 'stuck'
                                    """)

                                # Run pre-check
                                role = PreCheckRole()
                                role._reset_stuck_claimed(60)

                                # Task should be reset
                                task = get_task("stuck")
                                assert task["queue"] == "incoming"
                                assert task["claimed_by"] is None


class TestPreCheckNoAutoAcceptOrchestratorImpl:
    """Tests that pre-check does NOT auto-accept orchestrator_impl tasks."""

    def test_orchestrator_impl_task_not_auto_accepted(self, mock_config, initialized_db):
        """An orchestrator_impl task in provisional with 0 commits should be rejected, not auto-accepted."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.roles.pre_check.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        with patch('orchestrator.roles.pre_check.get_pre_check_config', return_value={
                            'require_commits': True,
                            'max_attempts_before_planning': 3,
                            'claim_timeout_minutes': 60,
                        }):
                            with patch.dict(os.environ, {
                                'AGENT_NAME': 'test-pre-check',
                                'AGENT_ID': '0',
                                'AGENT_ROLE': 'pre_check',
                                'PARENT_PROJECT': str(mock_config.parent),
                                'WORKTREE': str(mock_config.parent),
                                'SHARED_DIR': str(mock_config / 'shared'),
                                'ORCHESTRATOR_DIR': str(mock_config),
                            }):
                                from orchestrator.roles.pre_check import PreCheckRole
                                from orchestrator.db import create_task, claim_task, submit_completion, get_task

                                # Setup: create and submit an orchestrator_impl task with 0 commits
                                create_task(
                                    task_id="orchval1",
                                    file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-orchval1.md"),
                                    role="orchestrator_impl",
                                )
                                claim_task()
                                submit_completion("orchval1", commits_count=0, turns_used=20)

                                # Create the task file
                                prov_dir = mock_config / "shared" / "queue" / "provisional"
                                prov_dir.mkdir(parents=True, exist_ok=True)
                                (prov_dir / "TASK-orchval1.md").write_text(
                                    "# [TASK-orchval1] Orchestrator impl task\n"
                                    "ROLE: orchestrator_impl\n"
                                )

                                # Run pre-check
                                role = PreCheckRole()
                                result = role.run()
                                assert result == 0

                                # Task should NOT be in done (auto-accepted), should be rejected back to incoming
                                task = get_task("orchval1")
                                assert task["queue"] != "done", (
                                    "orchestrator_impl task should NOT be auto-accepted"
                                )
                                assert task["queue"] == "incoming", (
                                    f"Expected orchestrator_impl task to be rejected to incoming, "
                                    f"but found in {task['queue']}"
                                )

    def test_orchestrator_impl_task_with_commits_accepted(self, mock_config, initialized_db):
        """An orchestrator_impl task with commits should be accepted normally."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.roles.pre_check.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        with patch('orchestrator.roles.pre_check.get_pre_check_config', return_value={
                            'require_commits': True,
                            'max_attempts_before_planning': 3,
                            'claim_timeout_minutes': 60,
                        }):
                            with patch.dict(os.environ, {
                                'AGENT_NAME': 'test-pre-check',
                                'AGENT_ID': '0',
                                'AGENT_ROLE': 'pre_check',
                                'PARENT_PROJECT': str(mock_config.parent),
                                'WORKTREE': str(mock_config.parent),
                                'SHARED_DIR': str(mock_config / 'shared'),
                                'ORCHESTRATOR_DIR': str(mock_config),
                            }):
                                from orchestrator.roles.pre_check import PreCheckRole
                                from orchestrator.db import create_task, claim_task, submit_completion, get_task

                                # Setup: create and submit an orchestrator_impl task WITH commits
                                create_task(
                                    task_id="orchval2",
                                    file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-orchval2.md"),
                                    role="orchestrator_impl",
                                )
                                claim_task()
                                submit_completion("orchval2", commits_count=3, turns_used=20)

                                # Create the task file
                                prov_dir = mock_config / "shared" / "queue" / "provisional"
                                prov_dir.mkdir(parents=True, exist_ok=True)
                                (prov_dir / "TASK-orchval2.md").write_text(
                                    "# [TASK-orchval2] Orchestrator impl task with commits\n"
                                    "ROLE: orchestrator_impl\n"
                                )

                                # Run pre-check
                                role = PreCheckRole()
                                result = role.run()
                                assert result == 0

                                # Task should be accepted (has commits)
                                task = get_task("orchval2")
                                assert task["queue"] == "done"

    def test_orchestrator_impl_burned_out_gets_recycled(self, mock_config, initialized_db):
        """An orchestrator_impl task with 0 commits and high turns gets recycled by validator.

        Now that orchestrator_impl tasks go through the same burn-out detection
        as regular tasks, a task with 0 submodule commits and 100+ turns should
        be recycled, not just rejected.
        """
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.roles.pre_check.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        with patch('orchestrator.roles.pre_check.get_pre_check_config', return_value={
                            'require_commits': True,
                            'max_attempts_before_planning': 3,
                            'claim_timeout_minutes': 60,
                        }):
                            with patch.dict(os.environ, {
                                'AGENT_NAME': 'test-validator',
                                'AGENT_ID': '0',
                                'AGENT_ROLE': 'validator',
                                'PARENT_PROJECT': str(mock_config.parent),
                                'WORKTREE': str(mock_config.parent),
                                'SHARED_DIR': str(mock_config / 'shared'),
                                'ORCHESTRATOR_DIR': str(mock_config),
                            }):
                                from orchestrator.roles.pre_check import PreCheckRole
                                from orchestrator.db import create_task, claim_task, submit_completion, get_task

                                # Setup: create and submit an orchestrator_impl task with 0 commits and 100 turns
                                create_task(
                                    task_id="orchburn1",
                                    file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-orchburn1.md"),
                                    role="orchestrator_impl",
                                )
                                claim_task()
                                submit_completion("orchburn1", commits_count=0, turns_used=100)

                                # Create the task file
                                prov_dir = mock_config / "shared" / "queue" / "provisional"
                                prov_dir.mkdir(parents=True, exist_ok=True)
                                (prov_dir / "TASK-orchburn1.md").write_text(
                                    "# [TASK-orchburn1] Burned orchestrator impl task\n"
                                    "ROLE: orchestrator_impl\nPRIORITY: P1\nBRANCH: main\n\n"
                                    "## Context\nOrchestrator task that burned out.\n\n"
                                    "## Acceptance Criteria\n- [ ] Done\n"
                                )

                                # Run validator
                                role = PreCheckRole()
                                result = role.run()
                                assert result == 0

                                # Task should be recycled (burned out: 0 commits, 100 turns >= 80)
                                task = get_task("orchburn1")
                                assert task["queue"] == "recycled", (
                                    f"Expected burned orchestrator_impl task to be recycled, "
                                    f"but found in {task['queue']}"
                                )
