"""Tests for AGENT_NAME and agent env vars in spawn and Claude invocation."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestSpawnAgentEnv:
    """Tests that spawn_agent passes AGENT_NAME in the environment."""

    @patch("orchestrator.scheduler.get_agents_runtime_dir")
    @patch("orchestrator.scheduler.get_port_env_vars", return_value={})
    @patch("orchestrator.scheduler.find_parent_project")
    @patch("orchestrator.scheduler.get_orchestrator_dir")
    @patch("orchestrator.scheduler.get_worktree_path")
    @patch("subprocess.Popen")
    def test_spawn_agent_sets_agent_name(
        self,
        mock_popen,
        mock_worktree,
        mock_orch_dir,
        mock_parent,
        mock_ports,
        mock_agents_dir,
        tmp_path,
    ):
        """spawn_agent should include AGENT_NAME in the subprocess env."""
        mock_parent.return_value = tmp_path
        mock_orch_dir.return_value = tmp_path / ".orchestrator"
        mock_worktree.return_value = tmp_path / "worktree"
        mock_agents_dir.return_value = tmp_path / ".orchestrator" / "agents"

        # Create required directories
        (tmp_path / ".orchestrator" / "agents" / "impl-agent-1").mkdir(parents=True)

        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        from orchestrator.scheduler import spawn_agent

        spawn_agent("impl-agent-1", 0, "implementer", {})

        # Verify Popen was called with env containing AGENT_NAME
        call_kwargs = mock_popen.call_args
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")
        assert env is not None, "env should be passed to Popen"
        assert env["AGENT_NAME"] == "impl-agent-1"

    @patch("orchestrator.scheduler.get_agents_runtime_dir")
    @patch("orchestrator.scheduler.get_port_env_vars", return_value={})
    @patch("orchestrator.scheduler.find_parent_project")
    @patch("orchestrator.scheduler.get_orchestrator_dir")
    @patch("orchestrator.scheduler.get_worktree_path")
    @patch("subprocess.Popen")
    def test_spawn_agent_env_matches_agent_config(
        self,
        mock_popen,
        mock_worktree,
        mock_orch_dir,
        mock_parent,
        mock_ports,
        mock_agents_dir,
        tmp_path,
    ):
        """spawn_agent env should include AGENT_ID, AGENT_ROLE, and other vars."""
        mock_parent.return_value = tmp_path
        mock_orch_dir.return_value = tmp_path / ".orchestrator"
        mock_worktree.return_value = tmp_path / "worktree"
        mock_agents_dir.return_value = tmp_path / ".orchestrator" / "agents"

        (tmp_path / ".orchestrator" / "agents" / "test-agent").mkdir(parents=True)

        mock_process = MagicMock()
        mock_process.pid = 99999
        mock_popen.return_value = mock_process

        from orchestrator.scheduler import spawn_agent

        spawn_agent("test-agent", 3, "orchestrator_impl", {})

        call_kwargs = mock_popen.call_args
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")
        assert env["AGENT_NAME"] == "test-agent"
        assert env["AGENT_ID"] == "3"
        assert env["AGENT_ROLE"] == "orchestrator_impl"
        assert env["PARENT_PROJECT"] == str(tmp_path)


class TestBuildClaudeEnv:
    """Tests that _build_claude_env includes agent vars for hooks."""

    def _make_role(self, agent_name="test-agent", agent_id=1, role="implementer"):
        """Create a BaseRole instance with mocked env vars."""
        env = {
            "AGENT_NAME": agent_name,
            "AGENT_ID": str(agent_id),
            "AGENT_ROLE": role,
            "PARENT_PROJECT": "/tmp/project",
            "WORKTREE": "/tmp/worktree",
            "SHARED_DIR": "/tmp/shared",
            "ORCHESTRATOR_DIR": "/tmp/orch",
        }
        with patch.dict(os.environ, env):
            from orchestrator.roles.base import BaseRole

            # BaseRole is abstract, so create a concrete subclass
            class TestRole(BaseRole):
                def run(self):
                    return 0

            return TestRole()

    def test_build_claude_env_includes_agent_name(self):
        """_build_claude_env should set AGENT_NAME in returned env."""
        role = self._make_role(agent_name="impl-agent-1")
        env = role._build_claude_env()
        assert env["AGENT_NAME"] == "impl-agent-1"

    def test_build_claude_env_includes_all_agent_vars(self):
        """_build_claude_env should set all agent-specific env vars."""
        role = self._make_role(agent_name="my-agent", agent_id=5, role="tester")
        env = role._build_claude_env()
        assert env["AGENT_NAME"] == "my-agent"
        assert env["AGENT_ID"] == "5"
        assert env["AGENT_ROLE"] == "tester"
        assert "PARENT_PROJECT" in env
        assert "WORKTREE" in env
        assert "SHARED_DIR" in env
        assert "ORCHESTRATOR_DIR" in env

    def test_build_claude_env_inherits_parent_env(self):
        """_build_claude_env should include vars from the parent process."""
        role = self._make_role()
        with patch.dict(os.environ, {"MY_CUSTOM_VAR": "hello"}):
            env = role._build_claude_env()
            assert env.get("MY_CUSTOM_VAR") == "hello"

    @patch("subprocess.run")
    def test_invoke_claude_passes_env(self, mock_run):
        """invoke_claude should pass env with AGENT_NAME to subprocess."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        role = self._make_role(agent_name="hook-test-agent")
        role.invoke_claude("test prompt")

        call_kwargs = mock_run.call_args
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")
        assert env is not None, "env should be passed to subprocess.run"
        assert env["AGENT_NAME"] == "hook-test-agent"

    @patch("subprocess.Popen")
    def test_invoke_claude_streaming_passes_env(self, mock_popen, tmp_path):
        """invoke_claude with stdout_log should pass env to Popen."""
        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_proc.stderr = iter([])
        mock_proc.returncode = 0
        mock_proc.wait = MagicMock()
        mock_popen.return_value = mock_proc

        role = self._make_role(agent_name="stream-agent")
        stdout_log = tmp_path / "test.log"
        role.invoke_claude("test prompt", stdout_log=stdout_log)

        call_kwargs = mock_popen.call_args
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")
        assert env is not None, "env should be passed to Popen"
        assert env["AGENT_NAME"] == "stream-agent"

    def test_build_claude_env_includes_current_task_id(self):
        """_build_claude_env should include CURRENT_TASK_ID when set."""
        role = self._make_role(agent_name="task-agent")
        role.current_task_id = "abc12345"
        env = role._build_claude_env()
        assert env["CURRENT_TASK_ID"] == "abc12345"

    def test_build_claude_env_omits_current_task_id_when_none(self):
        """_build_claude_env should not include CURRENT_TASK_ID when not set."""
        role = self._make_role(agent_name="idle-agent")
        env = role._build_claude_env()
        assert "CURRENT_TASK_ID" not in env
