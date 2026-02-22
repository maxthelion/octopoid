"""Tests for the taskless agent job spawn path.

Covers:
- prepare_job_directory: creates correct directory structure
- spawn_job_agent: calls prepare_job_directory and invoke_claude, registers PID
- get_spawn_strategy: returns spawn_job_agent for taskless agents
- _run_agent_job: re-raises spawn failures (no silent "completed OK")
- _run_job: logs failure when spawn raises
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from orchestrator.scheduler import (
    AgentContext,
    get_spawn_strategy,
    prepare_job_directory,
    spawn_implementer,
    spawn_job_agent,
)
from orchestrator.state_utils import AgentState


# =============================================================================
# prepare_job_directory
# =============================================================================


class TestPrepareJobDirectory:
    """prepare_job_directory creates the correct layout for taskless agents."""

    def _make_agent_dir(self, tmp_path: Path) -> Path:
        """Create a minimal agent directory with prompt.md and scripts/."""
        agent_dir = tmp_path / "agents" / "codebase-analyst"
        agent_dir.mkdir(parents=True)
        (agent_dir / "prompt.md").write_text(
            "# Analyst\nDo some analysis.\n\n## Global Instructions\n$global_instructions\n"
        )
        scripts = agent_dir / "scripts"
        scripts.mkdir()
        (scripts / "guard.sh").write_text("#!/usr/bin/env bash\necho ok\n")
        (scripts / "find-large-files.sh").write_text("#!/usr/bin/env bash\necho files\n")
        return agent_dir

    def test_creates_worktree_directory(self, tmp_path):
        """prepare_job_directory creates a worktree/ subdir for Claude's cwd."""
        agent_dir = self._make_agent_dir(tmp_path)
        jobs_dir = tmp_path / "jobs"

        with (
            patch("orchestrator.scheduler.get_jobs_dir", return_value=jobs_dir),
            patch("orchestrator.scheduler.find_parent_project", return_value=tmp_path),
            patch("orchestrator.scheduler.get_global_instructions_path",
                  return_value=tmp_path / "nonexistent_gi.md"),
            patch("orchestrator.scheduler._get_server_url_from_config", return_value="http://localhost:9787"),
        ):
            job_dir = prepare_job_directory(
                "codebase_analyst",
                {"agent_dir": str(agent_dir)},
            )

        worktree = job_dir / "worktree"
        assert worktree.exists()
        assert worktree.is_dir()

    def test_creates_scripts_directory(self, tmp_path):
        """prepare_job_directory copies scripts from agent_dir/scripts/."""
        agent_dir = self._make_agent_dir(tmp_path)
        jobs_dir = tmp_path / "jobs"

        with (
            patch("orchestrator.scheduler.get_jobs_dir", return_value=jobs_dir),
            patch("orchestrator.scheduler.find_parent_project", return_value=tmp_path),
            patch("orchestrator.scheduler.get_global_instructions_path",
                  return_value=tmp_path / "nonexistent_gi.md"),
            patch("orchestrator.scheduler._get_server_url_from_config", return_value="http://localhost:9787"),
        ):
            job_dir = prepare_job_directory(
                "codebase_analyst",
                {"agent_dir": str(agent_dir)},
            )

        scripts_dir = job_dir / "scripts"
        assert scripts_dir.exists()
        assert (scripts_dir / "guard.sh").exists()
        assert (scripts_dir / "find-large-files.sh").exists()
        # Scripts must be executable
        assert (scripts_dir / "guard.sh").stat().st_mode & 0o111

    def test_creates_env_sh_without_task_fields(self, tmp_path):
        """prepare_job_directory writes env.sh with job vars but no TASK_ID/TASK_BRANCH."""
        agent_dir = self._make_agent_dir(tmp_path)
        jobs_dir = tmp_path / "jobs"

        with (
            patch("orchestrator.scheduler.get_jobs_dir", return_value=jobs_dir),
            patch("orchestrator.scheduler.find_parent_project", return_value=tmp_path),
            patch("orchestrator.scheduler.get_global_instructions_path",
                  return_value=tmp_path / "nonexistent_gi.md"),
            patch("orchestrator.scheduler._get_server_url_from_config", return_value="http://localhost:9787"),
        ):
            job_dir = prepare_job_directory(
                "codebase_analyst",
                {"agent_dir": str(agent_dir)},
            )

        env_sh = job_dir / "env.sh"
        assert env_sh.exists()
        content = env_sh.read_text()
        assert "ORCHESTRATOR_PYTHONPATH" in content
        assert "AGENT_NAME" in content
        assert "RESULT_FILE" in content
        # Task-specific vars must NOT be present
        assert "TASK_ID" not in content
        assert "TASK_BRANCH" not in content
        assert "WORKTREE=" not in content

    def test_creates_prompt_md(self, tmp_path):
        """prepare_job_directory renders prompt.md from the agent directory."""
        agent_dir = self._make_agent_dir(tmp_path)
        gi_path = tmp_path / "global_instructions.md"
        gi_path.write_text("Be careful.")
        jobs_dir = tmp_path / "jobs"

        with (
            patch("orchestrator.scheduler.get_jobs_dir", return_value=jobs_dir),
            patch("orchestrator.scheduler.find_parent_project", return_value=tmp_path),
            patch("orchestrator.scheduler.get_global_instructions_path", return_value=gi_path),
            patch("orchestrator.scheduler._get_server_url_from_config", return_value="http://localhost:9787"),
        ):
            job_dir = prepare_job_directory(
                "codebase_analyst",
                {"agent_dir": str(agent_dir)},
            )

        prompt = (job_dir / "prompt.md").read_text()
        assert "Be careful." in prompt  # global instructions substituted

    def test_raises_when_agent_dir_missing_scripts(self, tmp_path):
        """prepare_job_directory raises ValueError when scripts/ is absent."""
        agent_dir = tmp_path / "bad-agent"
        agent_dir.mkdir()
        (agent_dir / "prompt.md").write_text("# Bad agent\n")
        # No scripts/ subdirectory

        with (
            patch("orchestrator.scheduler.get_jobs_dir", return_value=tmp_path / "jobs"),
            patch("orchestrator.scheduler.find_parent_project", return_value=tmp_path),
            patch("orchestrator.scheduler.get_global_instructions_path",
                  return_value=tmp_path / "gi.md"),
            patch("orchestrator.scheduler._get_server_url_from_config", return_value="http://localhost:9787"),
        ):
            with pytest.raises(ValueError, match="scripts not found"):
                prepare_job_directory("bad-agent", {"agent_dir": str(agent_dir)})

    def test_raises_when_prompt_md_missing(self, tmp_path):
        """prepare_job_directory raises ValueError when prompt.md is absent."""
        agent_dir = tmp_path / "no-prompt-agent"
        agent_dir.mkdir()
        scripts = agent_dir / "scripts"
        scripts.mkdir()
        (scripts / "run.sh").write_text("#!/bin/bash\necho hi\n")
        # No prompt.md

        with (
            patch("orchestrator.scheduler.get_jobs_dir", return_value=tmp_path / "jobs"),
            patch("orchestrator.scheduler.find_parent_project", return_value=tmp_path),
            patch("orchestrator.scheduler.get_global_instructions_path",
                  return_value=tmp_path / "gi.md"),
            patch("orchestrator.scheduler._get_server_url_from_config", return_value="http://localhost:9787"),
        ):
            with pytest.raises(ValueError, match="prompt.md not found"):
                prepare_job_directory("no-prompt-agent", {"agent_dir": str(agent_dir)})


# =============================================================================
# spawn_job_agent
# =============================================================================


class TestSpawnJobAgent:
    """spawn_job_agent orchestrates directory prep, invocation, and PID registration."""

    def _make_ctx(self, tmp_path: Path, agent_dir: Path) -> AgentContext:
        state_path = tmp_path / "state.json"
        return AgentContext(
            agent_config={
                "agent_dir": str(agent_dir),
                "blueprint_name": "codebase_analyst",
                "role": "analyse",
                "lightweight": True,
            },
            agent_name="codebase_analyst",
            role="analyse",
            interval=86400,
            state=AgentState(),
            state_path=state_path,
            # claimed_task is None (taskless job)
        )

    def test_spawn_job_agent_calls_prepare_and_invoke(self, tmp_path):
        """spawn_job_agent calls prepare_job_directory and invoke_claude."""
        agent_dir = tmp_path / "agent"
        ctx = self._make_ctx(tmp_path, agent_dir)
        fake_job_dir = tmp_path / "jobs" / "codebase_analyst-20260101T000000"

        with (
            patch("orchestrator.scheduler.prepare_job_directory",
                  return_value=fake_job_dir) as mock_prepare,
            patch("orchestrator.scheduler.invoke_claude", return_value=12345) as mock_invoke,
            patch("orchestrator.scheduler.register_instance_pid") as mock_reg,
            patch("orchestrator.scheduler.mark_started",
                  return_value=AgentState(running=True, pid=12345)),
            patch("orchestrator.scheduler.save_state"),
            patch("orchestrator.scheduler._next_instance_name", return_value="codebase_analyst-1"),
        ):
            pid = spawn_job_agent(ctx)

        assert pid == 12345
        mock_prepare.assert_called_once_with("codebase_analyst", ctx.agent_config)
        mock_invoke.assert_called_once_with(fake_job_dir, ctx.agent_config)

    def test_spawn_job_agent_registers_pid_with_empty_task_id(self, tmp_path):
        """spawn_job_agent registers PID with empty task_id (no task to track)."""
        agent_dir = tmp_path / "agent"
        ctx = self._make_ctx(tmp_path, agent_dir)
        fake_job_dir = tmp_path / "jobs" / "codebase_analyst-20260101T000000"

        with (
            patch("orchestrator.scheduler.prepare_job_directory", return_value=fake_job_dir),
            patch("orchestrator.scheduler.invoke_claude", return_value=99),
            patch("orchestrator.scheduler.register_instance_pid") as mock_reg,
            patch("orchestrator.scheduler.mark_started",
                  return_value=AgentState(running=True, pid=99)),
            patch("orchestrator.scheduler.save_state"),
            patch("orchestrator.scheduler._next_instance_name", return_value="codebase_analyst-1"),
        ):
            spawn_job_agent(ctx)

        # Empty task_id ensures check_and_update_finished_agents just cleans up PID
        mock_reg.assert_called_once_with("codebase_analyst", 99, "", "codebase_analyst-1")

    def test_spawn_job_agent_sets_agent_mode_to_job(self, tmp_path):
        """spawn_job_agent marks state with agent_mode=job (not scripts)."""
        agent_dir = tmp_path / "agent"
        ctx = self._make_ctx(tmp_path, agent_dir)
        fake_job_dir = tmp_path / "jobs" / "codebase_analyst-20260101T000000"
        started_state = AgentState(running=True, pid=55)

        saved_states = []

        with (
            patch("orchestrator.scheduler.prepare_job_directory", return_value=fake_job_dir),
            patch("orchestrator.scheduler.invoke_claude", return_value=55),
            patch("orchestrator.scheduler.register_instance_pid"),
            patch("orchestrator.scheduler.mark_started", return_value=started_state),
            patch("orchestrator.scheduler.save_state",
                  side_effect=lambda s, p: saved_states.append(s)),
            patch("orchestrator.scheduler._next_instance_name", return_value="codebase_analyst-1"),
        ):
            spawn_job_agent(ctx)

        assert saved_states, "save_state should have been called"
        saved = saved_states[0]
        assert saved.extra.get("agent_mode") == "job"
        assert "job_dir" in saved.extra


# =============================================================================
# _run_agent_job — exception propagation
# =============================================================================


class TestRunAgentJobExceptionPropagation:
    """_run_agent_job re-raises spawn failures so _run_job can log correctly."""

    def test_spawn_failure_propagates_out_of_run_agent_job(self, tmp_path):
        """A spawn failure raised inside strategy() propagates out of _run_agent_job."""
        from orchestrator.jobs import _run_agent_job, JobContext

        job_def = {
            "name": "codebase_analyst",
            "type": "agent",
            "interval": 86400,
            "agent_config": {
                "role": "analyse",
                "spawn_mode": "scripts",
                "lightweight": True,
                "agent_dir": str(tmp_path / "nonexistent-agent"),
            },
        }
        ctx = JobContext(scheduler_state={})

        state_path = tmp_path / "agents" / "codebase_analyst" / "state.json"
        state_path.parent.mkdir(parents=True)
        fake_state = AgentState()

        # _run_agent_job lazily imports from orchestrator.scheduler,
        # so patch at the scheduler module level.
        with (
            patch("orchestrator.jobs.count_running_instances", return_value=0),
            patch("orchestrator.scheduler.get_agent_state_path", return_value=state_path),
            patch("orchestrator.scheduler.load_state", return_value=fake_state),
            patch("orchestrator.scheduler.get_spawn_strategy") as mock_strategy,
        ):
            # Strategy raises to simulate a spawn failure
            mock_strategy.return_value = MagicMock(
                side_effect=ValueError("NoneType object is not subscriptable")
            )

            with pytest.raises(ValueError, match="NoneType object is not subscriptable"):
                _run_agent_job(job_def, ctx)

    def test_run_job_logs_failure_not_completed_ok(self, tmp_path):
        """_run_job logs FAILED (not 'completed OK') when agent spawn raises."""
        from orchestrator.jobs import _run_job, JobContext

        job_def = {
            "name": "codebase_analyst",
            "type": "agent",
            "interval": 86400,
            "agent_config": {
                "role": "analyse",
                "agent_dir": str(tmp_path / "nonexistent"),
            },
        }
        ctx = JobContext(scheduler_state={})

        logged_messages: list[str] = []

        def fake_debug_log(msg: str) -> None:
            logged_messages.append(msg)

        with (
            patch("orchestrator.jobs._run_agent_job",
                  side_effect=RuntimeError("spawn exploded")),
            patch("orchestrator.jobs._debug_log", side_effect=fake_debug_log),
        ):
            _run_job(job_def, ctx)

        failure_msgs = [m for m in logged_messages if "FAILED" in m]
        ok_msgs = [m for m in logged_messages if "completed OK" in m]
        assert failure_msgs, "Expected a FAILED log message"
        assert not ok_msgs, "Should NOT log 'completed OK' when spawn fails"
