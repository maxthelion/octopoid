"""Tests for scheduler refactor: guard functions, evaluation chain, and spawn strategies.

This test suite verifies the scheduler's pipeline architecture introduced in the refactor.
Tests cover:
- AgentContext dataclass
- All 6 guard functions (enabled, not_running, interval, backpressure, pre_check, claim_task)
- evaluate_agent guard chain
- get_spawn_strategy dispatch
- run_housekeeping fault isolation
- handle_agent_result_via_flow decision dispatch

The refactor was purely structural - no behavior changes.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, call

import pytest

from orchestrator.scheduler import (
    AGENT_GUARDS,
    HOUSEKEEPING_JOBS,
    AgentContext,
    evaluate_agent,
    get_spawn_strategy,
    guard_backpressure,
    guard_enabled,
    guard_interval,
    guard_not_running,
    guard_pre_check,
    handle_agent_result_via_flow,
    run_housekeeping,
    spawn_implementer,
    spawn_lightweight,
    spawn_worktree,
)
from orchestrator.state_utils import AgentState


# =============================================================================
# AgentContext Tests
# =============================================================================


class TestAgentContext:
    """Test AgentContext dataclass."""

    def test_create_with_all_fields(self, tmp_path):
        """Test creating an AgentContext with all fields populated."""
        state_path = tmp_path / "state.json"
        state = AgentState(running=False, pid=None)

        ctx = AgentContext(
            agent_config={"name": "test-agent", "role": "implement"},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=state,
            state_path=state_path,
            claimed_task={"id": "task-123", "title": "Test task"},
        )

        assert ctx.agent_name == "test-agent"
        assert ctx.role == "implement"
        assert ctx.interval == 300
        assert ctx.state.running is False
        assert ctx.state.pid is None
        assert ctx.state_path == state_path
        assert ctx.claimed_task == {"id": "task-123", "title": "Test task"}

    def test_claimed_task_defaults_to_none(self, tmp_path):
        """Test that claimed_task defaults to None when not provided."""
        state_path = tmp_path / "state.json"
        state = AgentState()

        ctx = AgentContext(
            agent_config={},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=state,
            state_path=state_path,
        )

        assert ctx.claimed_task is None


# =============================================================================
# Guard Function Tests
# =============================================================================


class TestGuardEnabled:
    """Test guard_enabled function."""

    def test_paused_agent_returns_false(self, tmp_path):
        """Test that a paused agent is blocked."""
        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"paused": True},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_enabled(ctx)

        assert proceed is False
        assert reason == "paused"

    def test_enabled_agent_returns_true(self, tmp_path):
        """Test that an enabled (non-paused) agent passes."""
        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"paused": False},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_enabled(ctx)

        assert proceed is True
        assert reason == ""

    def test_agent_without_paused_key_returns_true(self, tmp_path):
        """Test that an agent without a paused key is treated as enabled."""
        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_enabled(ctx)

        assert proceed is True
        assert reason == ""


class TestGuardNotRunning:
    """Test guard_not_running function."""

    def test_not_running_idle_returns_true(self, tmp_path):
        """Test that an idle agent (not running) passes."""
        state_path = tmp_path / "state.json"
        state = AgentState(running=False, pid=None)

        ctx = AgentContext(
            agent_config={},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=state,
            state_path=state_path,
        )

        proceed, reason = guard_not_running(ctx)

        assert proceed is True
        assert reason == ""

    @patch("orchestrator.scheduler.is_process_running")
    def test_not_running_alive_returns_false(self, mock_is_running, tmp_path):
        """Test that an agent with a running PID is blocked."""
        mock_is_running.return_value = True

        state_path = tmp_path / "state.json"
        state = AgentState(running=True, pid=12345)

        ctx = AgentContext(
            agent_config={},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=state,
            state_path=state_path,
        )

        proceed, reason = guard_not_running(ctx)

        assert proceed is False
        assert "still running" in reason
        assert "12345" in reason
        mock_is_running.assert_called_once_with(12345)

    @patch("orchestrator.scheduler.is_process_running")
    @patch("orchestrator.scheduler.save_state")
    @patch("orchestrator.scheduler.mark_finished")
    def test_not_running_crashed_returns_true_and_updates_state(
        self, mock_mark_finished, mock_save_state, mock_is_running, tmp_path
    ):
        """Test that an agent marked running but with dead PID is cleaned up."""
        mock_is_running.return_value = False
        finished_state = AgentState(running=False, pid=None, last_exit_code=1)
        mock_mark_finished.return_value = finished_state

        state_path = tmp_path / "state.json"
        state = AgentState(running=True, pid=99999)

        ctx = AgentContext(
            agent_config={},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=state,
            state_path=state_path,
        )

        proceed, reason = guard_not_running(ctx)

        assert proceed is True
        assert reason == ""
        mock_is_running.assert_called_once_with(99999)
        mock_mark_finished.assert_called_once()
        mock_save_state.assert_called_once()


class TestGuardInterval:
    """Test guard_interval function."""

    @patch("orchestrator.scheduler.is_overdue")
    def test_interval_due_returns_true(self, mock_is_overdue, tmp_path):
        """Test that an overdue agent passes the interval guard."""
        mock_is_overdue.return_value = True

        state_path = tmp_path / "state.json"
        state = AgentState()

        ctx = AgentContext(
            agent_config={},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=state,
            state_path=state_path,
        )

        proceed, reason = guard_interval(ctx)

        assert proceed is True
        assert reason == ""
        mock_is_overdue.assert_called_once_with(state, 300)

    @patch("orchestrator.scheduler.is_overdue")
    def test_interval_not_due_returns_false(self, mock_is_overdue, tmp_path):
        """Test that a non-overdue agent is blocked."""
        mock_is_overdue.return_value = False

        state_path = tmp_path / "state.json"
        state = AgentState()

        ctx = AgentContext(
            agent_config={},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=state,
            state_path=state_path,
        )

        proceed, reason = guard_interval(ctx)

        assert proceed is False
        assert reason == "not due yet"


class TestGuardBackpressure:
    """Test guard_backpressure function."""

    @patch("orchestrator.backpressure.count_queue", return_value=0)
    def test_backpressure_no_tasks_returns_false(self, mock_count, tmp_path):
        """Test that no tasks in incoming queue returns False."""
        state_path = tmp_path / "state.json"
        state = AgentState()

        ctx = AgentContext(
            agent_config={"claim_from": "incoming"},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=state,
            state_path=state_path,
        )

        proceed, reason = guard_backpressure(ctx)

        assert proceed is False
        assert "no_tasks" in reason

    @patch("orchestrator.backpressure.can_claim_task", return_value=(False, "wip limit reached"))
    @patch("orchestrator.backpressure.count_queue", return_value=3)
    def test_backpressure_wip_limit_returns_false(self, mock_count, mock_can_claim, tmp_path):
        """Test that WIP limit blocks claiming."""
        state_path = tmp_path / "state.json"
        state = AgentState()

        ctx = AgentContext(
            agent_config={"claim_from": "incoming"},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=state,
            state_path=state_path,
        )

        proceed, reason = guard_backpressure(ctx)

        assert proceed is False
        assert "backpressure" in reason

    @patch("orchestrator.backpressure.can_claim_task", return_value=(True, ""))
    @patch("orchestrator.backpressure.count_queue", return_value=2)
    def test_backpressure_clear_returns_true(self, mock_count, mock_can_claim, tmp_path):
        """Test that available tasks and no WIP limit returns True."""
        state_path = tmp_path / "state.json"
        state = AgentState()

        ctx = AgentContext(
            agent_config={"claim_from": "incoming"},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=state,
            state_path=state_path,
        )

        proceed, reason = guard_backpressure(ctx)

        assert proceed is True
        assert reason == ""

    @patch("orchestrator.backpressure.count_queue", return_value=0)
    def test_backpressure_non_incoming_no_tasks_returns_false(self, mock_count, tmp_path):
        """Test that no tasks in non-incoming queue returns False."""
        state_path = tmp_path / "state.json"
        state = AgentState()

        ctx = AgentContext(
            agent_config={"claim_from": "provisional"},
            agent_name="gatekeeper-1",
            role="gatekeeper",
            interval=300,
            state=state,
            state_path=state_path,
        )

        proceed, reason = guard_backpressure(ctx)

        assert proceed is False
        assert "no_provisional_tasks" in reason

    @patch("orchestrator.backpressure.count_queue", return_value=1)
    def test_backpressure_non_incoming_with_tasks_returns_true(self, mock_count, tmp_path):
        """Test that tasks in non-incoming queue returns True."""
        state_path = tmp_path / "state.json"
        state = AgentState()

        ctx = AgentContext(
            agent_config={"claim_from": "provisional"},
            agent_name="gatekeeper-1",
            role="gatekeeper",
            interval=300,
            state=state,
            state_path=state_path,
        )

        proceed, reason = guard_backpressure(ctx)

        assert proceed is True
        assert reason == ""


class TestGuardPreCheck:
    """Test guard_pre_check function."""

    @patch("orchestrator.scheduler.run_pre_check")
    def test_pre_check_pass_returns_true(self, mock_run_pre_check, tmp_path):
        """Test that a passing pre-check returns True."""
        mock_run_pre_check.return_value = True

        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"pre_check": "some command"},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_pre_check(ctx)

        assert proceed is True
        assert reason == ""
        mock_run_pre_check.assert_called_once_with("test-agent", ctx.agent_config)

    @patch("orchestrator.scheduler.run_pre_check")
    def test_pre_check_fail_returns_false(self, mock_run_pre_check, tmp_path):
        """Test that a failing pre-check returns False."""
        mock_run_pre_check.return_value = False

        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"pre_check": "some command"},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_pre_check(ctx)

        assert proceed is False
        assert reason == "pre-check: no work"


# =============================================================================
# evaluate_agent Tests
# =============================================================================


class TestEvaluateAgent:
    """Test evaluate_agent guard chain."""

    def test_evaluate_agent_all_pass(self, tmp_path):
        """Test that all guards passing returns True."""
        # Create mock guards that all pass
        mock_guards = []
        for i in range(6):
            guard = Mock(return_value=(True, ""))
            guard.__name__ = f"guard_{i}"
            mock_guards.append(guard)

        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        with patch("orchestrator.scheduler.AGENT_GUARDS", mock_guards):
            result = evaluate_agent(ctx)

        assert result is True
        # Verify all guards were called
        for guard in mock_guards:
            guard.assert_called_once_with(ctx)

    def test_evaluate_agent_stops_at_first_fail(self, tmp_path):
        """Test that the chain stops at the first failing guard."""
        # First guard passes, second fails, rest not called
        guard1 = Mock(return_value=(True, ""))
        guard1.__name__ = "guard_1"

        guard2 = Mock(return_value=(False, "blocked"))
        guard2.__name__ = "guard_2"

        guard3 = Mock(return_value=(True, ""))
        guard3.__name__ = "guard_3"

        mock_guards = [guard1, guard2, guard3]

        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        with patch("orchestrator.scheduler.AGENT_GUARDS", mock_guards):
            result = evaluate_agent(ctx)

        assert result is False
        # First two guards called
        guard1.assert_called_once_with(ctx)
        guard2.assert_called_once_with(ctx)
        # Third guard NOT called
        guard3.assert_not_called()

    def test_evaluate_agent_first_guard_fails(self, tmp_path):
        """Test that if first guard fails, chain stops immediately."""
        guard1 = Mock(return_value=(False, "paused"))
        guard1.__name__ = "guard_enabled"

        guard2 = Mock(return_value=(True, ""))
        guard2.__name__ = "guard_2"

        mock_guards = [guard1, guard2]

        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        with patch("orchestrator.scheduler.AGENT_GUARDS", mock_guards):
            result = evaluate_agent(ctx)

        assert result is False
        guard1.assert_called_once_with(ctx)
        guard2.assert_not_called()


# =============================================================================
# get_spawn_strategy Tests
# =============================================================================


class TestGetSpawnStrategy:
    """Test get_spawn_strategy dispatch logic."""

    def test_get_spawn_strategy_implementer(self, tmp_path):
        """Test that implementer role with claimed_task returns spawn_implementer."""
        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"spawn_mode": "scripts"},
            agent_name="test-agent",
            role="implementer",
            interval=300,
            state=AgentState(),
            state_path=state_path,
            claimed_task={"id": "task-123"},
        )

        strategy = get_spawn_strategy(ctx)

        assert strategy == spawn_implementer

    def test_get_spawn_strategy_implementer_no_task_fallback(self, tmp_path):
        """Test that implementer without claimed_task falls back to spawn_worktree."""
        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={},
            agent_name="test-agent",
            role="implementer",
            interval=300,
            state=AgentState(),
            state_path=state_path,
            claimed_task=None,
        )

        strategy = get_spawn_strategy(ctx)

        assert strategy == spawn_worktree

    def test_get_spawn_strategy_lightweight(self, tmp_path):
        """Test that lightweight config returns spawn_lightweight."""
        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"lightweight": True},
            agent_name="test-agent",
            role="proposer",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        strategy = get_spawn_strategy(ctx)

        assert strategy == spawn_lightweight

    def test_get_spawn_strategy_worktree(self, tmp_path):
        """Test that non-lightweight, non-implementer returns spawn_worktree."""
        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"lightweight": False},
            agent_name="test-agent",
            role="breakdown",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        strategy = get_spawn_strategy(ctx)

        assert strategy == spawn_worktree


# =============================================================================
# run_housekeeping Tests
# =============================================================================


class TestRunHousekeeping:
    """Test run_housekeeping fault isolation."""

    def test_run_housekeeping_calls_all_jobs(self):
        """Test that all housekeeping jobs are called."""
        # Mock all jobs in the list
        mock_jobs = [Mock() for _ in HOUSEKEEPING_JOBS]

        with patch("orchestrator.scheduler.HOUSEKEEPING_JOBS", mock_jobs):
            run_housekeeping()

        # Verify each job was called
        for job in mock_jobs:
            job.assert_called_once()

    def test_run_housekeeping_continues_on_failure(self):
        """Test that one job raising exception doesn't stop others."""
        # Create mock jobs: one that fails, one that succeeds
        failing_job = Mock(side_effect=Exception("Job failed"))
        failing_job.__name__ = "failing_job"

        succeeding_job = Mock()
        succeeding_job.__name__ = "succeeding_job"

        mock_jobs = [failing_job, succeeding_job]

        with patch("orchestrator.scheduler.HOUSEKEEPING_JOBS", mock_jobs):
            # Should not raise exception
            run_housekeeping()

        # Both jobs were called
        failing_job.assert_called_once()
        succeeding_job.assert_called_once()

    def test_run_housekeeping_logs_failures(self):
        """Test that job failures are logged."""
        failing_job = Mock(side_effect=ValueError("Test error"))
        failing_job.__name__ = "test_failing_job"

        with patch("orchestrator.scheduler.HOUSEKEEPING_JOBS", [failing_job]):
            with patch("orchestrator.scheduler.debug_log") as mock_log:
                run_housekeeping()

                # Verify error was logged
                mock_log.assert_called()
                call_args = mock_log.call_args[0][0]
                assert "test_failing_job" in call_args
                assert "failed" in call_args.lower()


# =============================================================================
# handle_agent_result_via_flow Decision Tests
# =============================================================================


def _make_flow_mock(runs: list | None = None) -> MagicMock:
    """Build a minimal flow mock with one transition from 'provisional'."""
    transition = MagicMock()
    transition.runs = runs or ["merge_pr"]
    transition.conditions = []

    flow = MagicMock()
    flow.get_transitions_from.return_value = [transition]
    return flow


def _make_sdk_mock(queue: str = "provisional") -> MagicMock:
    """Build a minimal SDK mock."""
    sdk = MagicMock()
    sdk.tasks.get.return_value = {"id": "TASK-test", "queue": queue, "flow": "default"}
    return sdk


class TestHandleAgentResultViaFlowDecisions:
    """Test decision dispatch in handle_agent_result_via_flow."""

    def _run(
        self,
        task_dir: Path,
        decision: str | None,
        status: str = "success",
        runs: list | None = None,
    ) -> tuple[MagicMock, MagicMock]:
        """Helper: write result.json and invoke the function with mocked dependencies."""
        result = {"status": status}
        if decision is not None:
            result["decision"] = decision
        (task_dir / "result.json").write_text(json.dumps(result))

        mock_sdk = _make_sdk_mock()
        mock_flow = _make_flow_mock(runs=runs)

        with (
            patch("orchestrator.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.flow.load_flow", return_value=mock_flow),
            patch("orchestrator.steps.execute_steps") as mock_execute,
            patch("orchestrator.steps.reject_with_feedback") as mock_reject,
        ):
            handle_agent_result_via_flow("TASK-test", "gatekeeper-1", task_dir)
            return mock_execute, mock_reject

    def test_approve_executes_steps(self, tmp_path: Path) -> None:
        """Explicit 'approve' decision must execute transition steps."""
        mock_execute, mock_reject = self._run(tmp_path, decision="approve")

        mock_execute.assert_called_once()
        mock_reject.assert_not_called()

    def test_reject_calls_reject_with_feedback(self, tmp_path: Path) -> None:
        """Explicit 'reject' decision must call reject_with_feedback and not execute steps."""
        mock_execute, mock_reject = self._run(tmp_path, decision="reject")

        mock_reject.assert_called_once()
        mock_execute.assert_not_called()

    def test_none_decision_does_nothing(self, tmp_path: Path) -> None:
        """Missing 'decision' field must not execute steps and not reject."""
        mock_execute, mock_reject = self._run(tmp_path, decision=None)

        mock_execute.assert_not_called()
        mock_reject.assert_not_called()

    def test_unknown_decision_does_nothing(self, tmp_path: Path) -> None:
        """Unknown 'decision' value (e.g. 'banana') must not execute steps or reject."""
        mock_execute, mock_reject = self._run(tmp_path, decision="banana")

        mock_execute.assert_not_called()
        mock_reject.assert_not_called()

    def test_unknown_decision_logs_warning(self, tmp_path: Path) -> None:
        """Unknown 'decision' value should log a warning mentioning the value."""
        result = {"status": "success", "decision": "banana"}
        (tmp_path / "result.json").write_text(json.dumps(result))

        mock_sdk = _make_sdk_mock()
        mock_flow = _make_flow_mock()

        with (
            patch("orchestrator.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.flow.load_flow", return_value=mock_flow),
            patch("orchestrator.steps.execute_steps"),
            patch("orchestrator.steps.reject_with_feedback"),
            patch("orchestrator.scheduler.debug_log") as mock_log,
        ):
            handle_agent_result_via_flow("TASK-test", "gatekeeper-1", tmp_path)

        log_messages = [c.args[0] for c in mock_log.call_args_list]
        assert any("banana" in m for m in log_messages), (
            f"Expected 'banana' in log messages, got: {log_messages}"
        )

    def test_exception_moves_task_to_failed(self, tmp_path: Path) -> None:
        """When flow dispatch raises, task must be moved to 'failed' queue."""
        result = {"status": "success", "decision": "approve"}
        (tmp_path / "result.json").write_text(json.dumps(result))

        mock_sdk = _make_sdk_mock()
        # Make execute_steps raise to trigger the except branch
        with (
            patch("orchestrator.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.flow.load_flow", side_effect=RuntimeError("pnpm not in PATH")),
        ):
            handle_agent_result_via_flow("TASK-test", "implementer-1", tmp_path)

        mock_sdk.tasks.update.assert_called_once()
        call_kwargs = mock_sdk.tasks.update.call_args
        assert call_kwargs.args[0] == "TASK-test"
        assert call_kwargs.kwargs.get("queue") == "failed"
        assert "pnpm not in PATH" in call_kwargs.kwargs.get("execution_notes", "")

    def test_exception_logs_full_traceback(self, tmp_path: Path) -> None:
        """When flow dispatch raises, full traceback must be logged."""
        result = {"status": "success", "decision": "approve"}
        (tmp_path / "result.json").write_text(json.dumps(result))

        mock_sdk = _make_sdk_mock()
        with (
            patch("orchestrator.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.flow.load_flow", side_effect=RuntimeError("boom")),
            patch("orchestrator.scheduler.debug_log") as mock_log,
        ):
            handle_agent_result_via_flow("TASK-test", "implementer-1", tmp_path)

        log_messages = [c.args[0] for c in mock_log.call_args_list]
        # The traceback log should contain the exception type or traceback header
        assert any("Traceback" in m or "RuntimeError" in m for m in log_messages), (
            f"Expected traceback in log messages, got: {log_messages}"
        )

    def test_exception_recovery_failure_is_handled(self, tmp_path: Path) -> None:
        """If moving to failed queue also fails, it should be caught and logged."""
        result = {"status": "success", "decision": "approve"}
        (tmp_path / "result.json").write_text(json.dumps(result))

        mock_sdk = _make_sdk_mock()
        mock_sdk.tasks.update.side_effect = RuntimeError("SDK unavailable")

        with (
            patch("orchestrator.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.flow.load_flow", side_effect=RuntimeError("pnpm not in PATH")),
            patch("orchestrator.scheduler.debug_log") as mock_log,
        ):
            # Should not raise even when the recovery itself fails
            handle_agent_result_via_flow("TASK-test", "implementer-1", tmp_path)

        log_messages = [c.args[0] for c in mock_log.call_args_list]
        assert any("Failed to move" in m for m in log_messages), (
            f"Expected 'Failed to move' in log messages, got: {log_messages}"
        )


# =============================================================================
# guard_pr_mergeable Tests
# =============================================================================


class TestGuardPrMergeable:
    """Test guard_pr_mergeable function."""

    def _make_ctx(self, tmp_path: Path, pr_number: int | None = 42) -> "AgentContext":
        """Helper to build a gatekeeper AgentContext with a claimed task."""
        from orchestrator.scheduler import AgentContext
        from orchestrator.state_utils import AgentState

        task = {"id": "TASK-test", "pr_number": pr_number}
        return AgentContext(
            agent_config={"spawn_mode": "scripts", "claim_from": "provisional"},
            agent_name="gatekeeper-1",
            role="gatekeeper",
            interval=300,
            state=AgentState(),
            state_path=tmp_path / "state.json",
            claimed_task=task,
        )

    def test_blocks_when_pr_is_conflicting(self, tmp_path: Path) -> None:
        """guard_pr_mergeable returns (False, 'pr_conflicts: ...') when PR has conflicts."""
        from orchestrator.scheduler import guard_pr_mergeable

        ctx = self._make_ctx(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"mergeable": "CONFLICTING"}'

        mock_sdk = MagicMock()

        with (
            patch("orchestrator.scheduler.subprocess.run", return_value=mock_result),
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
        ):
            proceed, reason = guard_pr_mergeable(ctx)

        assert proceed is False
        assert "pr_conflicts" in reason
        assert "42" in reason

    def test_passes_when_pr_is_mergeable(self, tmp_path: Path) -> None:
        """guard_pr_mergeable returns (True, '') when PR is MERGEABLE."""
        from orchestrator.scheduler import guard_pr_mergeable

        ctx = self._make_ctx(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"mergeable": "MERGEABLE"}'

        with patch("orchestrator.scheduler.subprocess.run", return_value=mock_result):
            proceed, reason = guard_pr_mergeable(ctx)

        assert proceed is True
        assert reason == ""

    def test_passes_when_no_pr_number(self, tmp_path: Path) -> None:
        """guard_pr_mergeable passes through when task has no PR yet."""
        from orchestrator.scheduler import guard_pr_mergeable

        ctx = self._make_ctx(tmp_path, pr_number=None)

        proceed, reason = guard_pr_mergeable(ctx)

        assert proceed is True
        assert reason == ""

    def test_passes_when_no_claimed_task(self, tmp_path: Path) -> None:
        """guard_pr_mergeable passes through when no task is claimed."""
        from orchestrator.scheduler import AgentContext, guard_pr_mergeable
        from orchestrator.state_utils import AgentState

        ctx = AgentContext(
            agent_config={"spawn_mode": "scripts", "claim_from": "provisional"},
            agent_name="gatekeeper-1",
            role="gatekeeper",
            interval=300,
            state=AgentState(),
            state_path=tmp_path / "state.json",
            claimed_task=None,
        )

        proceed, reason = guard_pr_mergeable(ctx)

        assert proceed is True
        assert reason == ""

    def test_conflicting_releases_claim_and_rejects(self, tmp_path: Path) -> None:
        """When CONFLICTING, guard releases the task claim and rejects back to incoming."""
        from orchestrator.scheduler import guard_pr_mergeable

        ctx = self._make_ctx(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"mergeable": "CONFLICTING"}'

        mock_sdk = MagicMock()

        with (
            patch("orchestrator.scheduler.subprocess.run", return_value=mock_result),
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
        ):
            guard_pr_mergeable(ctx)

        # Should have rejected the task
        mock_sdk.tasks.reject.assert_called_once()

    def test_guard_is_in_agent_guards_chain(self) -> None:
        """guard_pr_mergeable must be in AGENT_GUARDS after guard_claim_task."""
        from orchestrator.scheduler import AGENT_GUARDS, guard_claim_task, guard_pr_mergeable

        names = [g.__name__ for g in AGENT_GUARDS]
        assert "guard_pr_mergeable" in names

        # Must appear after guard_claim_task
        claim_idx = names.index("guard_claim_task")
        mergeable_idx = names.index("guard_pr_mergeable")
        assert mergeable_idx > claim_idx, (
            f"guard_pr_mergeable (idx {mergeable_idx}) must come after "
            f"guard_claim_task (idx {claim_idx})"
        )
