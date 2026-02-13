"""Tests for scheduler venv integrity check."""

import pytest
from unittest.mock import patch


class TestVerifyInstallPath:
    """Tests for _check_venv_integrity guard."""

    def test_normal_path_passes(self):
        """Normal orchestrator install path does not trigger exit."""
        normal_path = "/Users/dev/myproject/orchestrator/orchestrator/scheduler.py"
        with patch("orchestrator.scheduler.__file__", normal_path):
            from orchestrator.scheduler import _check_venv_integrity
            _check_venv_integrity()

    def test_agent_worktree_path_exits(self):
        """Detects orchestrator loaded from an agent worktree and exits."""
        hijacked_path = (
            "/Users/dev/myproject/.octopoid/runtime/agents/impl-1/worktree"
            "/orchestrator/orchestrator/scheduler.py"
        )
        with patch("orchestrator.scheduler.__file__", hijacked_path):
            from orchestrator.scheduler import _check_venv_integrity
            with pytest.raises(SystemExit) as exc_info:
                _check_venv_integrity()
            assert exc_info.value.code == 1

    def test_agents_without_worktree_passes(self):
        """Path containing 'agents' but not 'worktree' should not trigger."""
        ok_path = "/Users/dev/agents/orchestrator/orchestrator/scheduler.py"
        with patch("orchestrator.scheduler.__file__", ok_path):
            from orchestrator.scheduler import _check_venv_integrity
            _check_venv_integrity()

    def test_worktree_without_agents_passes(self):
        """Path containing 'worktree' but not 'agents' should not trigger."""
        ok_path = "/Users/dev/worktree/orchestrator/orchestrator/scheduler.py"
        with patch("orchestrator.scheduler.__file__", ok_path):
            from orchestrator.scheduler import _check_venv_integrity
            _check_venv_integrity()

    def test_error_message_includes_path(self, capsys):
        """Exit message includes the offending path for debugging."""
        hijacked_path = (
            "/Users/dev/myproject/.octopoid/runtime/agents/impl-1/worktree"
            "/orchestrator/orchestrator/scheduler.py"
        )
        with patch("orchestrator.scheduler.__file__", hijacked_path):
            from orchestrator.scheduler import _check_venv_integrity
            with pytest.raises(SystemExit):
                _check_venv_integrity()
        captured = capsys.readouterr()
        assert "FATAL" in captured.err
        assert "agent worktree" in captured.err
