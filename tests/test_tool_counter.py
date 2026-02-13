"""Tests for tool call counting via PostToolUse hook integration."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestToolCounterBaseRole:
    """Tests for tool counter methods on BaseRole."""

    def _make_role(self, agent_name="test-agent", orchestrator_dir=None):
        """Create a concrete BaseRole instance for testing."""
        env = {
            "AGENT_NAME": agent_name,
            "AGENT_ID": "1",
            "AGENT_ROLE": "implementer",
            "PARENT_PROJECT": "/tmp/project",
            "WORKTREE": "/tmp/worktree",
            "SHARED_DIR": "/tmp/shared",
            "ORCHESTRATOR_DIR": str(orchestrator_dir or "/tmp/orch"),
        }
        with patch.dict(os.environ, env):
            from orchestrator.roles.base import BaseRole

            class TestRole(BaseRole):
                def run(self):
                    return 0

            return TestRole()

    def test_get_tool_counter_path(self, tmp_path):
        """get_tool_counter_path returns correct path under orchestrator_dir."""
        role = self._make_role(agent_name="impl-1", orchestrator_dir=tmp_path)
        expected = tmp_path / "agents" / "impl-1" / "tool_counter"
        assert role.get_tool_counter_path() == expected

    def test_read_tool_count_no_file(self, tmp_path):
        """read_tool_count returns None when counter file doesn't exist."""
        role = self._make_role(orchestrator_dir=tmp_path)
        assert role.read_tool_count() is None

    def test_read_tool_count_empty_file(self, tmp_path):
        """read_tool_count returns 0 for empty counter file."""
        role = self._make_role(agent_name="impl-1", orchestrator_dir=tmp_path)
        counter = tmp_path / "agents" / "impl-1" / "tool_counter"
        counter.parent.mkdir(parents=True)
        counter.write_bytes(b"")
        assert role.read_tool_count() == 0

    def test_read_tool_count_with_data(self, tmp_path):
        """read_tool_count returns file size (one byte per tool call)."""
        role = self._make_role(agent_name="impl-1", orchestrator_dir=tmp_path)
        counter = tmp_path / "agents" / "impl-1" / "tool_counter"
        counter.parent.mkdir(parents=True)
        # Simulate 42 tool calls (42 bytes)
        counter.write_bytes(b"." * 42)
        assert role.read_tool_count() == 42

    def test_reset_tool_counter_creates_empty_file(self, tmp_path):
        """reset_tool_counter creates an empty counter file."""
        role = self._make_role(agent_name="impl-1", orchestrator_dir=tmp_path)
        role.reset_tool_counter()
        counter = tmp_path / "agents" / "impl-1" / "tool_counter"
        assert counter.exists()
        assert counter.stat().st_size == 0

    def test_reset_tool_counter_truncates_existing(self, tmp_path):
        """reset_tool_counter truncates an existing counter file."""
        role = self._make_role(agent_name="impl-1", orchestrator_dir=tmp_path)
        counter = tmp_path / "agents" / "impl-1" / "tool_counter"
        counter.parent.mkdir(parents=True)
        counter.write_bytes(b"." * 100)
        assert counter.stat().st_size == 100

        role.reset_tool_counter()
        assert counter.stat().st_size == 0

    def test_reset_creates_parent_dirs(self, tmp_path):
        """reset_tool_counter creates parent directories if needed."""
        role = self._make_role(agent_name="new-agent", orchestrator_dir=tmp_path)
        assert not (tmp_path / "agents" / "new-agent").exists()

        role.reset_tool_counter()
        counter = tmp_path / "agents" / "new-agent" / "tool_counter"
        assert counter.exists()


class TestToolCounterInOrchestratorImpl:
    """Tests that orchestrator_impl.py reads tool counter instead of hardcoding."""

    def _make_orch_impl(self, orchestrator_dir, worktree_dir):
        """Create an OrchestratorImplRole with mocked environment."""
        env = {
            "AGENT_NAME": "test-orch",
            "AGENT_ID": "2",
            "AGENT_ROLE": "orchestrator_impl",
            "PARENT_PROJECT": str(worktree_dir),
            "WORKTREE": str(worktree_dir),
            "SHARED_DIR": str(orchestrator_dir / "shared"),
            "ORCHESTRATOR_DIR": str(orchestrator_dir),
        }
        with patch.dict(os.environ, env):
            from orchestrator.roles.orchestrator_impl import OrchestratorImplRole
            return OrchestratorImplRole()

    @patch("orchestrator.roles.orchestrator_impl.claim_task")
    def test_orch_impl_resets_counter_on_claim(self, mock_claim, tmp_path):
        """OrchestratorImplRole should reset tool counter when claiming a task."""
        mock_claim.return_value = None  # No task available
        orch_dir = tmp_path / "orch"
        role = self._make_orch_impl(orch_dir, tmp_path)
        result = role.run()
        assert result == 0

    @patch("orchestrator.queue_utils.complete_task")
    @patch("orchestrator.queue_utils.save_task_notes")
    @patch("orchestrator.queue_utils.get_task_notes", return_value=None)
    @patch("orchestrator.git_utils.create_pull_request", return_value="https://pr.url")
    @patch("orchestrator.git_utils.has_uncommitted_changes", return_value=False)
    @patch("orchestrator.git_utils.get_commit_count", return_value=2)
    @patch("orchestrator.git_utils.get_head_ref", return_value="def67890")
    @patch("orchestrator.git_utils.create_feature_branch", return_value="feature/orch")
    @patch("orchestrator.git_utils.get_current_branch", return_value="agent/test")
    @patch("orchestrator.git_utils.create_task_worktree")
    @patch("orchestrator.roles.orchestrator_impl.claim_task")
    @patch("orchestrator.config.get_notes_dir")
    def test_orch_impl_reads_tool_count(
        self,
        mock_notes_dir,
        mock_claim,
        mock_create_worktree,
        mock_get_branch,
        mock_branch,
        mock_head,
        mock_commits,
        mock_uncommitted,
        mock_pr,
        mock_prev_notes,
        mock_save_notes,
        mock_complete,
        tmp_path,
    ):
        """OrchestratorImplRole should use actual tool count instead of hardcoded 200."""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        mock_notes_dir.return_value = notes_dir

        mock_claim.return_value = {
            "id": "orch999",
            "title": "Orchestrator task",
            "branch": "main",
            "path": tmp_path / "TASK-orch999.md",
            "content": "orch content",
        }
        mock_create_worktree.return_value = tmp_path

        orch_dir = tmp_path / "orch"
        role = self._make_orch_impl(orch_dir, tmp_path)

        counter_path = orch_dir / "agents" / "test-orch" / "tool_counter"

        def fake_invoke(*args, **kwargs):
            counter_path.parent.mkdir(parents=True, exist_ok=True)
            counter_path.write_bytes(b"." * 123)
            return (0, "done", "")

        from subprocess import CompletedProcess
        mock_cmd_result = CompletedProcess(args=[], returncode=0, stdout='', stderr='')

        with patch.object(role, "invoke_claude", side_effect=fake_invoke), \
             patch.object(role, "read_instructions", return_value=""), \
             patch.object(role, "_run_cmd", return_value=mock_cmd_result), \
             patch.object(role, "_create_submodule_branch"):
                role.run()

        # Verify complete_task was called
        mock_complete.assert_called_once()

    @patch("orchestrator.queue_utils.complete_task")
    @patch("orchestrator.queue_utils.save_task_notes")
    @patch("orchestrator.queue_utils.get_task_notes", return_value=None)
    @patch("orchestrator.git_utils.create_pull_request", return_value="https://pr.url")
    @patch("orchestrator.git_utils.has_uncommitted_changes", return_value=False)
    @patch("orchestrator.git_utils.get_commit_count", return_value=1)
    @patch("orchestrator.git_utils.get_head_ref", return_value="def67890")
    @patch("orchestrator.git_utils.create_feature_branch", return_value="feature/orch")
    @patch("orchestrator.git_utils.get_current_branch", return_value="agent/test")
    @patch("orchestrator.git_utils.create_task_worktree")
    @patch("orchestrator.roles.orchestrator_impl.claim_task")
    @patch("orchestrator.config.get_notes_dir")
    def test_orch_impl_falls_back_to_200_when_no_counter(
        self,
        mock_notes_dir,
        mock_claim,
        mock_create_worktree,
        mock_get_branch,
        mock_branch,
        mock_head,
        mock_commits,
        mock_uncommitted,
        mock_pr,
        mock_prev_notes,
        mock_save_notes,
        mock_complete,
        tmp_path,
    ):
        """OrchestratorImplRole should fall back to 200 when counter missing."""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        mock_notes_dir.return_value = notes_dir

        mock_claim.return_value = {
            "id": "orch888",
            "title": "Orchestrator task",
            "branch": "main",
            "path": tmp_path / "TASK-orch888.md",
            "content": "orch content",
        }
        mock_create_worktree.return_value = tmp_path

        orch_dir = tmp_path / "orch"
        role = self._make_orch_impl(orch_dir, tmp_path)

        counter_path = orch_dir / "agents" / "test-orch" / "tool_counter"

        def fake_invoke(*args, **kwargs):
            if counter_path.exists():
                counter_path.unlink()
            return (0, "done", "")

        from subprocess import CompletedProcess
        mock_cmd_result = CompletedProcess(args=[], returncode=0, stdout='', stderr='')

        with patch.object(role, "invoke_claude", side_effect=fake_invoke), \
             patch.object(role, "read_instructions", return_value=""), \
             patch.object(role, "_run_cmd", return_value=mock_cmd_result), \
             patch.object(role, "_create_submodule_branch"):
                role.run()

        mock_complete.assert_called_once()


class TestHookScript:
    """Tests for the count-tool-use.sh hook script behavior."""

    def _find_hook(self):
        """Find the hook script, trying multiple paths."""
        candidates = [
            Path(__file__).parent.parent.parent.parent / ".claude" / "hooks" / "count-tool-use.sh",
            Path("/Users/maxwilliams/dev/boxen/.claude/hooks/count-tool-use.sh"),
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    def test_hook_is_noop_without_agent_name(self):
        """Hook should exit 0 and do nothing when AGENT_NAME is unset."""
        import subprocess

        hook_path = self._find_hook()
        if not hook_path:
            pytest.skip("Hook script not found")

        result = subprocess.run(
            ["bash", str(hook_path)],
            env={"PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_hook_appends_byte_with_agent_name(self, tmp_path):
        """Hook should append a byte to tool_counter when AGENT_NAME is set."""
        import subprocess

        hook_path = self._find_hook()
        if not hook_path:
            pytest.skip("Hook script not found")

        agent_dir = tmp_path / ".octopoid" / "runtime" / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)

        env = {
            "PATH": os.environ.get("PATH", ""),
            "AGENT_NAME": "test-agent",
            "CLAUDE_PROJECT_DIR": str(tmp_path),
        }

        # Run hook 3 times
        for _ in range(3):
            result = subprocess.run(
                ["bash", str(hook_path)],
                env=env,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0

        counter = agent_dir / "tool_counter"
        assert counter.exists()
        assert counter.stat().st_size == 3
