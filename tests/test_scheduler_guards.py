"""Tests for scheduler guard bug fixes.

Covers:
- guard_not_running: should not call mark_finished when PID is None
- check_and_update_finished_agents: reads result.json for pure-function agents
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from orchestrator.scheduler import (
    AgentContext,
    check_and_update_finished_agents,
    guard_not_running,
)
from orchestrator.state_utils import AgentState


# =============================================================================
# Bug 1: guard_not_running with running=True but pid=None
# =============================================================================


class TestGuardNotRunningNoPid:
    """guard_not_running must not call mark_finished when PID is None."""

    def _make_ctx(self, tmp_path: Path, running: bool, pid: int | None) -> AgentContext:
        state_path = tmp_path / "state.json"
        return AgentContext(
            agent_config={},
            agent_name="implementer-1",
            role="implement",
            interval=300,
            state=AgentState(
                running=running,
                pid=pid,
                consecutive_failures=0,
                total_runs=2,
                total_successes=2,
                total_failures=0,
            ),
            state_path=state_path,
        )

    @patch("orchestrator.scheduler.save_state")
    @patch("orchestrator.scheduler.mark_finished")
    def test_running_true_pid_none_does_not_call_mark_finished(
        self, mock_mark_finished, mock_save_state, tmp_path
    ):
        """When running=True and pid=None, mark_finished must NOT be called."""
        ctx = self._make_ctx(tmp_path, running=True, pid=None)

        proceed, reason = guard_not_running(ctx)

        assert proceed is True
        assert reason == ""
        mock_mark_finished.assert_not_called()

    @patch("orchestrator.scheduler.save_state")
    @patch("orchestrator.scheduler.mark_finished")
    def test_running_true_pid_none_clears_running_flag(
        self, mock_mark_finished, mock_save_state, tmp_path
    ):
        """When running=True and pid=None, running must be cleared to False."""
        ctx = self._make_ctx(tmp_path, running=True, pid=None)

        guard_not_running(ctx)

        # State must be updated to running=False
        assert ctx.state.running is False
        assert ctx.state.pid is None

    @patch("orchestrator.scheduler.save_state")
    @patch("orchestrator.scheduler.mark_finished")
    def test_running_true_pid_none_does_not_increment_failures(
        self, mock_mark_finished, mock_save_state, tmp_path
    ):
        """When running=True and pid=None, failure counters must not be incremented."""
        ctx = self._make_ctx(tmp_path, running=True, pid=None)
        original_consecutive = ctx.state.consecutive_failures
        original_total_failures = ctx.state.total_failures

        guard_not_running(ctx)

        assert ctx.state.consecutive_failures == original_consecutive
        assert ctx.state.total_failures == original_total_failures

    @patch("orchestrator.scheduler.save_state")
    @patch("orchestrator.scheduler.mark_finished")
    def test_running_true_pid_none_preserves_extra(
        self, mock_mark_finished, mock_save_state, tmp_path
    ):
        """When clearing running=True/pid=None, extra dict must be preserved."""
        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={},
            agent_name="implementer-1",
            role="implement",
            interval=300,
            state=AgentState(
                running=True,
                pid=None,
                extra={"task_dir": "/some/task", "agent_mode": "scripts"},
            ),
            state_path=state_path,
        )

        guard_not_running(ctx)

        assert ctx.state.extra == {"task_dir": "/some/task", "agent_mode": "scripts"}

    @patch("orchestrator.scheduler.save_state")
    @patch("orchestrator.scheduler.mark_finished")
    def test_running_true_pid_none_saves_state(
        self, mock_mark_finished, mock_save_state, tmp_path
    ):
        """When running=True and pid=None, save_state must be called."""
        ctx = self._make_ctx(tmp_path, running=True, pid=None)

        guard_not_running(ctx)

        mock_save_state.assert_called_once()

    @patch("orchestrator.scheduler.is_process_running")
    @patch("orchestrator.scheduler.save_state")
    @patch("orchestrator.scheduler.mark_finished")
    def test_running_true_with_dead_pid_calls_mark_finished(
        self, mock_mark_finished, mock_save_state, mock_is_running, tmp_path
    ):
        """When running=True and pid exists but process is dead, mark_finished IS called."""
        mock_is_running.return_value = False
        finished_state = AgentState(running=False, pid=None, last_exit_code=1)
        mock_mark_finished.return_value = finished_state

        ctx = self._make_ctx(tmp_path, running=True, pid=99999)
        original_state = ctx.state  # capture before mutation

        proceed, reason = guard_not_running(ctx)

        assert proceed is True
        mock_mark_finished.assert_called_once_with(original_state, 1)
        mock_save_state.assert_called_once()

    def test_not_running_idle_returns_true(self, tmp_path):
        """Idle agent (running=False, pid=None) passes guard unchanged."""
        ctx = self._make_ctx(tmp_path, running=False, pid=None)

        proceed, reason = guard_not_running(ctx)

        assert proceed is True
        assert reason == ""


# =============================================================================
# Bug 2: check_and_update_finished_agents reads result.json for pure-function agents
# =============================================================================


class TestCheckAndUpdateFinishedAgents:
    """check_and_update_finished_agents must use result.json when no exit_code file exists."""

    def _make_state(self, task_dir: Path | None = None) -> AgentState:
        extra: dict = {}
        if task_dir is not None:
            extra = {"task_dir": str(task_dir), "agent_mode": "scripts", "current_task_id": ""}
        return AgentState(
            running=True,
            pid=12345,
            total_runs=1,
            total_successes=0,
            total_failures=0,
            consecutive_failures=0,
            extra=extra,
        )

    def _run_check(
        self,
        tmp_path: Path,
        task_dir: Path | None,
        result_outcome: str | None,
        read_exit_code: int | None,
    ) -> list[AgentState]:
        """Helper: set up mocks, run check_and_update_finished_agents, return saved states.

        Agent dirs live in agents_dir. Task dir lives outside agents_dir to avoid
        it being iterated as an agent.
        """
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        agent_dir = agents_dir / "implementer-1"
        agent_dir.mkdir()

        if task_dir is not None and result_outcome is not None:
            (task_dir / "result.json").write_text(json.dumps({"outcome": result_outcome}))

        state = self._make_state(task_dir)
        saved_states: list[AgentState] = []

        with (
            patch("orchestrator.scheduler.get_agents_runtime_dir", return_value=agents_dir),
            patch("orchestrator.scheduler.get_agent_state_path", return_value=agent_dir / "state.json"),
            patch("orchestrator.scheduler.load_state", return_value=state),
            patch("orchestrator.scheduler.is_process_running", return_value=False),
            patch("orchestrator.scheduler.read_agent_exit_code", return_value=read_exit_code),
            patch("orchestrator.scheduler.save_state", side_effect=lambda s, p: saved_states.append(s)),
            patch("orchestrator.scheduler.handle_agent_result"),
            patch("orchestrator.scheduler.handle_agent_result_via_flow"),
        ):
            check_and_update_finished_agents()

        return saved_states

    def test_success_result_json_gives_exit_code_0(self, tmp_path):
        """A result.json with outcome='done' must produce exit_code=0 (success)."""
        task_dir = tmp_path / "task"
        task_dir.mkdir()

        saved = self._run_check(tmp_path, task_dir, result_outcome="done", read_exit_code=None)

        assert len(saved) == 1
        assert saved[0].last_exit_code == 0
        assert saved[0].total_successes == 1
        assert saved[0].total_failures == 0
        assert saved[0].consecutive_failures == 0

    def test_submitted_result_json_gives_exit_code_0(self, tmp_path):
        """A result.json with outcome='submitted' must produce exit_code=0."""
        task_dir = tmp_path / "task"
        task_dir.mkdir()

        saved = self._run_check(tmp_path, task_dir, result_outcome="submitted", read_exit_code=None)

        assert saved[0].last_exit_code == 0
        assert saved[0].total_successes == 1

    def test_failed_result_json_gives_exit_code_1(self, tmp_path):
        """A result.json with outcome='failed' must produce exit_code=1 (failure)."""
        task_dir = tmp_path / "task"
        task_dir.mkdir()

        saved = self._run_check(tmp_path, task_dir, result_outcome="failed", read_exit_code=None)

        assert saved[0].last_exit_code == 1
        assert saved[0].total_failures == 1
        assert saved[0].total_successes == 0

    def test_missing_result_json_gives_exit_code_1(self, tmp_path):
        """No result.json (and no exit_code file) must produce exit_code=1."""
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        # No result.json created — _run_check only writes result.json when result_outcome is not None

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        agent_dir = agents_dir / "implementer-1"
        agent_dir.mkdir()
        state = self._make_state(task_dir)
        saved_states: list[AgentState] = []

        with (
            patch("orchestrator.scheduler.get_agents_runtime_dir", return_value=agents_dir),
            patch("orchestrator.scheduler.get_agent_state_path", return_value=agent_dir / "state.json"),
            patch("orchestrator.scheduler.load_state", return_value=state),
            patch("orchestrator.scheduler.is_process_running", return_value=False),
            patch("orchestrator.scheduler.read_agent_exit_code", return_value=None),
            patch("orchestrator.scheduler.save_state", side_effect=lambda s, p: saved_states.append(s)),
            patch("orchestrator.scheduler.handle_agent_result"),
            patch("orchestrator.scheduler.handle_agent_result_via_flow"),
        ):
            check_and_update_finished_agents()

        assert saved_states[0].last_exit_code == 1
        assert saved_states[0].total_failures == 1

    def test_no_task_dir_gives_exit_code_1(self, tmp_path):
        """When no task_dir in extra and no exit_code file, assume crash (exit_code=1)."""
        saved = self._run_check(tmp_path, task_dir=None, result_outcome=None, read_exit_code=None)

        assert saved[0].last_exit_code == 1

    def test_explicit_exit_code_file_is_used_when_present(self, tmp_path):
        """When an exit_code file exists, it takes priority over result.json."""
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        # result.json says failure but exit_code file says 0
        (task_dir / "result.json").write_text(json.dumps({"outcome": "failed"}))

        saved = self._run_check(tmp_path, task_dir, result_outcome=None, read_exit_code=0)

        # exit_code file wins — we pass read_exit_code=0 so result.json is never read
        assert saved[0].last_exit_code == 0
        assert saved[0].total_successes == 1
