"""Tests for scheduler venv install path verification."""

import pytest
from pathlib import Path
from unittest.mock import patch


class TestVerifyInstallPath:
    """Tests for verify_install_path guard."""

    def test_normal_path_passes(self):
        """Normal orchestrator install path does not trigger exit."""
        normal_path = "/Users/dev/myproject/orchestrator/orchestrator/__init__.py"
        with patch("orchestrator.scheduler._orchestrator_pkg") as mock_pkg:
            mock_pkg.__file__ = normal_path
            from orchestrator.scheduler import verify_install_path
            # Should not raise or exit
            verify_install_path()

    def test_agent_worktree_path_exits(self):
        """Detects orchestrator loaded from an agent worktree and exits."""
        hijacked_path = (
            "/Users/dev/myproject/.orchestrator/agents/impl-1/worktree"
            "/orchestrator/orchestrator/__init__.py"
        )
        with patch("orchestrator.scheduler._orchestrator_pkg") as mock_pkg:
            mock_pkg.__file__ = hijacked_path
            from orchestrator.scheduler import verify_install_path
            with pytest.raises(SystemExit) as exc_info:
                verify_install_path()
            assert exc_info.value.code == 1

    def test_agents_without_worktree_passes(self):
        """Path containing 'agents' but not 'worktree' should not trigger."""
        ok_path = "/Users/dev/agents/orchestrator/orchestrator/__init__.py"
        with patch("orchestrator.scheduler._orchestrator_pkg") as mock_pkg:
            mock_pkg.__file__ = ok_path
            from orchestrator.scheduler import verify_install_path
            verify_install_path()

    def test_worktree_without_agents_passes(self):
        """Path containing 'worktree' but not 'agents' should not trigger."""
        ok_path = "/Users/dev/worktree/orchestrator/orchestrator/__init__.py"
        with patch("orchestrator.scheduler._orchestrator_pkg") as mock_pkg:
            mock_pkg.__file__ = ok_path
            from orchestrator.scheduler import verify_install_path
            verify_install_path()

    def test_error_message_includes_path(self, capsys):
        """Exit message includes the offending path for debugging."""
        hijacked_path = (
            "/Users/dev/myproject/.orchestrator/agents/impl-1/worktree"
            "/orchestrator/orchestrator/__init__.py"
        )
        with patch("orchestrator.scheduler._orchestrator_pkg") as mock_pkg:
            mock_pkg.__file__ = hijacked_path
            from orchestrator.scheduler import verify_install_path
            with pytest.raises(SystemExit):
                verify_install_path()
        captured = capsys.readouterr()
        assert "FATAL" in captured.err
        assert "agent worktree" in captured.err
        assert hijacked_path in captured.err
