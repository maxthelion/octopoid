"""Tests for the create_task.py script.

These tests verify that the canonical task creation script:
- Accepts valid inputs and creates tasks correctly
- Validates role, priority, and required fields
- Returns proper exit codes (0 for success, 1 for errors)
- Outputs task ID on success

NOTE: Tests that verify file contents create real task files in the project queue
and clean them up afterward. This is necessary because the script runs as a subprocess
with its own config context.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def get_real_queue_path():
    """Get the real project queue directory."""
    # Navigate up from tests/ to orchestrator/ to main repo
    test_dir = Path(__file__).parent
    orchestrator_dir = test_dir.parent
    main_repo = orchestrator_dir.parent
    return main_repo / ".orchestrator" / "shared" / "queue"


@pytest.fixture
def script_path():
    """Path to the create_task.py script."""
    return Path(__file__).parent.parent / "scripts" / "create_task.py"


class TestCreateTaskScript:
    """Tests for the create_task.py CLI script."""

    def test_successful_task_creation(self, mock_config, initialized_db, script_path):
        """Script creates task with valid inputs and outputs task ID."""
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--title", "Test task for script",
                "--role", "implement",
                "--priority", "P1",
                "--branch", "main",
                "--context", "Test context",
                "--acceptance-criteria", "- [ ] Criterion 1\n- [ ] Criterion 2",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        task_id = result.stdout.strip()
        assert task_id.startswith("TASK-")

        # Verify task file was created in the real queue
        queue_dir = get_real_queue_path()
        task_path = queue_dir / "incoming" / f"{task_id}.md"

        try:
            assert task_path.exists()

            content = task_path.read_text()
            assert "Test task for script" in content
            assert "ROLE: implement" in content
            assert "PRIORITY: P1" in content
            assert "BRANCH: main" in content
            assert "Test context" in content
            assert "- [ ] Criterion 1" in content
        finally:
            # Clean up test task
            if task_path.exists():
                task_path.unlink()

    def test_invalid_role_rejected(self, mock_config, script_path):
        """Script rejects invalid role with exit code 1."""
        with patch('orchestrator.config.get_orchestrator_dir', return_value=mock_config):
            result = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "--title", "Test task",
                    "--role", "invalid_role",
                    "--priority", "P1",
                    "--branch", "main",
                    "--context", "Test context",
                    "--acceptance-criteria", "- [ ] Test",
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode != 0
            assert "invalid choice" in result.stderr.lower()

    def test_invalid_priority_rejected(self, mock_config, script_path):
        """Script rejects invalid priority with exit code 1."""
        with patch('orchestrator.config.get_orchestrator_dir', return_value=mock_config):
            result = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "--title", "Test task",
                    "--role", "implement",
                    "--priority", "P99",
                    "--branch", "main",
                    "--context", "Test context",
                    "--acceptance-criteria", "- [ ] Test",
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode != 0
            assert "invalid choice" in result.stderr.lower()

    def test_missing_title_rejected(self, mock_config, script_path):
        """Script rejects missing title with exit code 1."""
        with patch('orchestrator.config.get_orchestrator_dir', return_value=mock_config):
            result = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "--role", "implement",
                    "--priority", "P1",
                    "--branch", "main",
                    "--context", "Test context",
                    "--acceptance-criteria", "- [ ] Test",
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode != 0
            assert "required" in result.stderr.lower()

    def test_empty_title_rejected(self, mock_config, script_path):
        """Script rejects empty title with exit code 1."""
        with patch('orchestrator.config.get_orchestrator_dir', return_value=mock_config):
            result = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "--title", "   ",
                    "--role", "implement",
                    "--priority", "P1",
                    "--branch", "main",
                    "--context", "Test context",
                    "--acceptance-criteria", "- [ ] Test",
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode == 1
            assert "must not be empty" in result.stderr

    def test_empty_context_rejected(self, mock_config, script_path):
        """Script rejects empty context with exit code 1."""
        with patch('orchestrator.config.get_orchestrator_dir', return_value=mock_config):
            result = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "--title", "Test task",
                    "--role", "implement",
                    "--priority", "P1",
                    "--branch", "main",
                    "--context", "   ",
                    "--acceptance-criteria", "- [ ] Test",
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode == 1
            assert "must not be empty" in result.stderr

    def test_empty_acceptance_criteria_rejected(self, mock_config, script_path):
        """Script rejects empty acceptance criteria with exit code 1."""
        with patch('orchestrator.config.get_orchestrator_dir', return_value=mock_config):
            result = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "--title", "Test task",
                    "--role", "implement",
                    "--priority", "P1",
                    "--branch", "main",
                    "--context", "Test context",
                    "--acceptance-criteria", "   ",
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode == 1
            assert "must not be empty" in result.stderr

    def test_orchestrator_impl_role_accepted(self, mock_config, initialized_db, script_path):
        """Script accepts orchestrator_impl role."""
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--title", "Fix orchestrator bug test",
                "--role", "orchestrator_impl",
                "--priority", "P0",
                "--branch", "main",
                "--context", "Critical bug",
                "--acceptance-criteria", "- [ ] Bug fixed",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        task_id = result.stdout.strip()
        assert task_id.startswith("TASK-")

        # Clean up
        queue_dir = get_real_queue_path()
        task_path = queue_dir / "incoming" / f"{task_id}.md"
        if task_path.exists():
            task_path.unlink()

    def test_all_roles_accepted(self, mock_config, initialized_db, script_path):
        """Script accepts all valid roles."""
        valid_roles = [
            "implement",
            "test",
            "review",
            "breakdown",
            "orchestrator_impl",
        ]

        queue_dir = get_real_queue_path()
        task_ids_to_clean = []

        try:
            for role in valid_roles:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(script_path),
                        "--title", f"Test task for {role}",
                        "--role", role,
                        "--priority", "P1",
                        "--branch", "main",
                        "--context", f"Context for {role}",
                        "--acceptance-criteria", "- [ ] Done",
                    ],
                    capture_output=True,
                    text=True,
                )

                assert result.returncode == 0, f"Failed for role: {role}, stderr: {result.stderr}"
                task_id = result.stdout.strip()
                assert task_id.startswith("TASK-")
                task_ids_to_clean.append(task_id)
        finally:
            # Clean up all created tasks
            for task_id in task_ids_to_clean:
                task_path = queue_dir / "incoming" / f"{task_id}.md"
                if task_path.exists():
                    task_path.unlink()

    def test_priority_default_is_p1(self, mock_config, initialized_db, script_path):
        """Script defaults to P1 priority when not specified."""
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--title", "Test task default priority",
                "--role", "implement",
                "--branch", "main",
                "--context", "Test context",
                "--acceptance-criteria", "- [ ] Done",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        task_id = result.stdout.strip()

        queue_dir = get_real_queue_path()
        task_path = queue_dir / "incoming" / f"{task_id}.md"

        try:
            assert task_path.exists()
            content = task_path.read_text()
            assert "PRIORITY: P1" in content
        finally:
            if task_path.exists():
                task_path.unlink()

    def test_optional_blocked_by(self, mock_config, initialized_db, script_path):
        """Script accepts optional blocked_by parameter."""
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--title", "Blocked task test",
                "--role", "implement",
                "--branch", "main",
                "--context", "This depends on other tasks",
                "--acceptance-criteria", "- [ ] Done",
                "--blocked-by", "abc123,def456",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        task_id = result.stdout.strip()

        queue_dir = get_real_queue_path()
        task_path = queue_dir / "incoming" / f"{task_id}.md"

        try:
            assert task_path.exists()
            content = task_path.read_text()
            assert "BLOCKED_BY: abc123,def456" in content
        finally:
            if task_path.exists():
                task_path.unlink()

    def test_optional_checks(self, mock_config, initialized_db, script_path):
        """Script accepts optional checks parameter."""
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--title", "Task with checks test",
                "--role", "implement",
                "--branch", "main",
                "--context", "Requires gatekeeper checks",
                "--acceptance-criteria", "- [ ] Done",
                "--checks", "gk-testing,gk-architecture",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        task_id = result.stdout.strip()

        queue_dir = get_real_queue_path()
        task_path = queue_dir / "incoming" / f"{task_id}.md"

        try:
            assert task_path.exists()
            content = task_path.read_text()
            assert "CHECKS: gk-testing,gk-architecture" in content
        finally:
            if task_path.exists():
                task_path.unlink()
