"""Tests for orchestrator.roles.validator module."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import os


class TestValidatorRole:
    """Tests for ValidatorRole class."""

    def test_validator_requires_db_mode(self, mock_config):
        """Test that validator returns early if DB not enabled."""
        with patch('orchestrator.roles.validator.is_db_enabled', return_value=False):
            with patch.dict(os.environ, {
                'AGENT_NAME': 'test-validator',
                'AGENT_ID': '0',
                'AGENT_ROLE': 'validator',
                'PARENT_PROJECT': str(mock_config.parent),
                'WORKTREE': str(mock_config.parent),
                'SHARED_DIR': str(mock_config / 'shared'),
                'ORCHESTRATOR_DIR': str(mock_config),
            }):
                from orchestrator.roles.validator import ValidatorRole

                role = ValidatorRole()
                result = role.run()

                assert result == 0  # Exits cleanly

    def test_validator_accepts_task_with_commits(self, mock_config, initialized_db):
        """Test that validator accepts tasks with commits."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.roles.validator.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        with patch('orchestrator.roles.validator.get_validation_config', return_value={
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
                                from orchestrator.roles.validator import ValidatorRole
                                from orchestrator.db import create_task, claim_task, submit_completion, get_task

                                # Setup: create and submit a task with commits
                                create_task(task_id="valid1", file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-valid1.md"))
                                claim_task()
                                submit_completion("valid1", commits_count=3, turns_used=20)

                                # Create the task file
                                prov_dir = mock_config / "shared" / "queue" / "provisional"
                                prov_dir.mkdir(parents=True, exist_ok=True)
                                (prov_dir / "TASK-valid1.md").write_text("# [TASK-valid1] Test\n")

                                # Run validator
                                role = ValidatorRole()
                                result = role.run()

                                assert result == 0

                                # Task should be accepted (in done queue)
                                task = get_task("valid1")
                                assert task["queue"] == "done"

    def test_validator_rejects_task_without_commits(self, mock_config, initialized_db):
        """Test that validator rejects tasks without commits."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.roles.validator.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        with patch('orchestrator.roles.validator.get_validation_config', return_value={
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
                                from orchestrator.roles.validator import ValidatorRole
                                from orchestrator.db import create_task, claim_task, submit_completion, get_task

                                # Setup: create and submit a task with NO commits
                                create_task(task_id="nocommit", file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-nocommit.md"))
                                claim_task()
                                submit_completion("nocommit", commits_count=0, turns_used=20)

                                # Create the task file
                                prov_dir = mock_config / "shared" / "queue" / "provisional"
                                prov_dir.mkdir(parents=True, exist_ok=True)
                                (prov_dir / "TASK-nocommit.md").write_text("# [TASK-nocommit] Test\n")

                                # Run validator
                                role = ValidatorRole()
                                result = role.run()

                                assert result == 0

                                # Task should be rejected (back in incoming)
                                task = get_task("nocommit")
                                assert task["queue"] == "incoming"
                                assert task["attempt_count"] == 1

    def test_validator_escalates_after_max_attempts(self, mock_config, initialized_db):
        """Test that validator escalates tasks after max attempts."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.roles.validator.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.planning.is_db_enabled', return_value=True):
                        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                            with patch('orchestrator.planning.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                                with patch('orchestrator.roles.validator.get_validation_config', return_value={
                                    'require_commits': True,
                                    'max_attempts_before_planning': 2,  # Low threshold for test
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
                                        from orchestrator.roles.validator import ValidatorRole
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

                                        # Run validator
                                        role = ValidatorRole()
                                        result = role.run()

                                        assert result == 0

                                        # Task should be recycled (new behavior: recycle before escalate)
                                        task = get_task("escalate")
                                        assert task["queue"] == "recycled"

    def test_validator_accepts_without_commit_requirement(self, mock_config, initialized_db):
        """Test that validator accepts tasks when require_commits is False."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.roles.validator.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        with patch('orchestrator.roles.validator.get_validation_config', return_value={
                            'require_commits': False,  # Don't require commits
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
                                from orchestrator.roles.validator import ValidatorRole
                                from orchestrator.db import create_task, claim_task, submit_completion, get_task

                                # Setup: create and submit a task with NO commits
                                create_task(task_id="nocheck", file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-nocheck.md"))
                                claim_task()
                                submit_completion("nocheck", commits_count=0)

                                # Create the task file
                                prov_dir = mock_config / "shared" / "queue" / "provisional"
                                prov_dir.mkdir(parents=True, exist_ok=True)
                                (prov_dir / "TASK-nocheck.md").write_text("# [TASK-nocheck] Test\n")

                                # Run validator
                                role = ValidatorRole()
                                result = role.run()

                                assert result == 0

                                # Task should be accepted even without commits
                                task = get_task("nocheck")
                                assert task["queue"] == "done"


class TestValidatorHelpers:
    """Tests for validator helper methods."""

    def test_reset_stuck_claimed(self, mock_config, initialized_db):
        """Test resetting stuck claimed tasks via validator."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.roles.validator.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                    with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        with patch('orchestrator.roles.validator.get_validation_config', return_value={
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
                                from orchestrator.roles.validator import ValidatorRole
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

                                # Run validator
                                role = ValidatorRole()
                                role._reset_stuck_claimed(60)

                                # Task should be reset
                                task = get_task("stuck")
                                assert task["queue"] == "incoming"
                                assert task["claimed_by"] is None
