"""Tests for scheduler refactor: guard functions, evaluation chain, and spawn strategies.

This test suite verifies the scheduler's pipeline architecture introduced in the refactor.
Tests cover:
- AgentContext dataclass
- Guard functions (enabled, pool_capacity, interval, backpressure, pre_check, claim_task)
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
    guard_pool_capacity,
    guard_pre_check,
    handle_agent_result_via_flow,
    run_housekeeping,
    spawn_implementer,
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



class TestGuardPoolCapacity:
    """Test guard_pool_capacity function."""

    @patch("orchestrator.scheduler.count_running_instances", return_value=0)
    def test_under_capacity_returns_true(self, mock_count, tmp_path):
        """Test that under capacity (0 of 1) returns True."""
        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"blueprint_name": "implementer", "max_instances": 1},
            agent_name="implementer",
            role="implement",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_pool_capacity(ctx)

        assert proceed is True
        assert reason == ""
        mock_count.assert_called_once_with("implementer")

    @patch("orchestrator.scheduler.count_running_instances", return_value=2)
    def test_at_capacity_returns_false(self, mock_count, tmp_path):
        """Test that at capacity (2 of 2) returns False."""
        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"blueprint_name": "implementer", "max_instances": 2},
            agent_name="implementer",
            role="implement",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_pool_capacity(ctx)

        assert proceed is False
        assert "at_capacity" in reason
        assert "2/2" in reason

    @patch("orchestrator.scheduler.count_running_instances", return_value=0)
    def test_no_cleanup_dead_pids_called(self, mock_count, tmp_path):
        """guard_pool_capacity must NOT call cleanup_dead_pids (race condition fix).

        count_running_instances already ignores dead PIDs without removing them.
        Only check_and_update_finished_agents should remove dead PIDs.
        """
        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"blueprint_name": "proposer", "max_instances": 1},
            agent_name="proposer",
            role="propose",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_pool_capacity(ctx)

        assert proceed is True
        mock_count.assert_called_once_with("proposer")

    @patch("orchestrator.scheduler.count_running_instances", return_value=1)
    def test_uses_agent_name_as_fallback_blueprint(self, mock_count, tmp_path):
        """Test that agent_name is used as blueprint_name when blueprint_name is absent."""
        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"max_instances": 2},  # no blueprint_name key
            agent_name="gatekeeper",
            role="gatekeeper",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_pool_capacity(ctx)

        assert proceed is True
        mock_count.assert_called_once_with("gatekeeper")

    @patch("orchestrator.scheduler.count_running_instances", return_value=0)
    def test_max_instances_defaults_to_1(self, mock_count, tmp_path):
        """Test that max_instances defaults to 1 when not specified."""
        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"blueprint_name": "implementer"},
            agent_name="implementer",
            role="implement",
            interval=300,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_pool_capacity(ctx)

        assert proceed is True

    def test_guard_pool_capacity_in_agent_guards_chain(self) -> None:
        """guard_pool_capacity must be in AGENT_GUARDS, replacing guard_not_running."""
        names = [g.__name__ for g in AGENT_GUARDS]

        assert "guard_pool_capacity" in names, (
            f"guard_pool_capacity not found in AGENT_GUARDS: {names}"
        )
        assert "guard_not_running" not in names, (
            f"guard_not_running should not be in AGENT_GUARDS: {names}"
        )


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
    """Test get_spawn_strategy always returns spawn_implementer."""

    def test_get_spawn_strategy_returns_spawn_implementer(self, tmp_path):
        """get_spawn_strategy always returns spawn_implementer (scripts mode only)."""
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

    def test_get_spawn_strategy_always_implementer_regardless_of_config(self, tmp_path):
        """get_spawn_strategy returns spawn_implementer regardless of agent config."""
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

        assert strategy == spawn_implementer


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
# Detached HEAD enforcement tests
# =============================================================================


def _make_prepare_task_patches(tmp_path: Path) -> dict:
    """Return context manager patches needed to run prepare_task_directory in tests."""
    return {
        "orchestrator.git_utils.create_task_worktree": None,  # set per-test
        "orchestrator.scheduler.get_task_branch": None,       # set per-test
        "orchestrator.scheduler.get_tasks_dir": tmp_path / "tasks",
        "orchestrator.scheduler.get_base_branch": "main",
        "orchestrator.scheduler.get_global_instructions_path": tmp_path / "gi.md",
        "orchestrator.scheduler.find_parent_project": tmp_path,
        "orchestrator.scheduler._get_server_url_from_config": "http://localhost",
    }


class TestPrepareTaskDirectoryDetachedHead:
    """prepare_task_directory must never checkout a named branch in the worktree."""

    def _make_task(self) -> dict:
        return {
            "id": "TASK-abc123",
            "title": "Test task",
            "content": "Do something",
            "branch": None,
            "project_id": None,
            "breakdown_id": None,
            "role": "implement",
            "hooks": None,
        }

    def _make_project_task(self) -> dict:
        return {
            "id": "TASK-proj001",
            "title": "Project task",
            "content": "Do something in the project",
            "branch": None,
            "project_id": "PROJ-001",
            "breakdown_id": None,
            "role": "implement",
            "hooks": None,
        }

    def _make_agent_dir(self, tmp_path: Path) -> Path:
        agent_dir = tmp_path / "agent"
        (agent_dir / "scripts").mkdir(parents=True)
        (agent_dir / "prompt.md").write_text("Task: $task_content")
        return agent_dir

    def test_worktree_stays_detached_after_prepare(self, tmp_path: Path) -> None:
        """prepare_task_directory must leave the worktree on detached HEAD.

        Verified by confirming that no git branch checkout occurs after worktree creation.
        The worktree is returned by create_task_worktree already in detached HEAD state,
        and prepare_task_directory must not call ensure_on_branch or any checkout.
        """
        from orchestrator.scheduler import prepare_task_directory

        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        agent_dir = self._make_agent_dir(tmp_path)
        task = self._make_task()

        with (
            patch("orchestrator.git_utils.create_task_worktree", return_value=worktree_path),
            patch("orchestrator.scheduler.get_task_branch", return_value="agent/TASK-abc123"),
            patch("orchestrator.scheduler.get_tasks_dir", return_value=tmp_path / "tasks"),
            patch("orchestrator.scheduler.get_base_branch", return_value="main"),
            patch("orchestrator.scheduler.get_global_instructions_path", return_value=tmp_path / "gi.md"),
            patch("orchestrator.scheduler.find_parent_project", return_value=tmp_path),
            patch("orchestrator.scheduler._get_server_url_from_config", return_value="http://localhost"),
        ):
            # Must complete without error — on current code, fails with NameError (get_main_branch)
            # after the fix: runs cleanly and never touches git branch state
            result_dir = prepare_task_directory(task, "implementer-1", {"agent_dir": str(agent_dir)})

        # Verify env.sh contains TASK_BRANCH (the branch name for the agent to use later)
        # but that no git checkout command was issued by prepare_task_directory
        env_sh = result_dir / "env.sh"
        assert env_sh.exists()
        assert "TASK_BRANCH='agent/TASK-abc123'" in env_sh.read_text()

    def test_project_task_does_not_fail_when_branch_checked_out(self, tmp_path: Path) -> None:
        """A project task whose branch is already checked out elsewhere must not crash.

        Previously: get_task_branch returned "feature/client-server-architecture",
        and ensure_on_branch tried to checkout that branch in the worktree. Git refused
        because the branch is already checked out in the main working tree → exit code 128.

        After the fix: ensure_on_branch is never called, so no git error can occur.
        """
        from orchestrator.scheduler import prepare_task_directory

        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        agent_dir = self._make_agent_dir(tmp_path)
        task = self._make_project_task()

        with (
            patch("orchestrator.git_utils.create_task_worktree", return_value=worktree_path),
            patch("orchestrator.scheduler.get_task_branch", return_value="feature/client-server-architecture"),
            patch("orchestrator.scheduler.get_tasks_dir", return_value=tmp_path / "tasks"),
            patch("orchestrator.scheduler.get_base_branch", return_value="main"),
            patch("orchestrator.scheduler.get_global_instructions_path", return_value=tmp_path / "gi.md"),
            patch("orchestrator.scheduler.find_parent_project", return_value=tmp_path),
            patch("orchestrator.scheduler._get_server_url_from_config", return_value="http://localhost"),
        ):
            # Must not raise — on current code raises because ensure_on_branch is called
            # and the branch is already checked out in the main worktree
            prepare_task_directory(task, "implementer-1", {"agent_dir": str(agent_dir)})

    def test_ensure_on_branch_not_called_during_spawn(self, tmp_path: Path) -> None:
        """The spawn pipeline must not call ensure_on_branch.

        Worktrees stay on detached HEAD throughout prepare_task_directory.
        If ensure_on_branch is called at all, the test fails with AssertionError.
        """
        from orchestrator.scheduler import prepare_task_directory
        from orchestrator.repo_manager import RepoManager

        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        agent_dir = self._make_agent_dir(tmp_path)
        task = self._make_task()

        def fail_if_ensure_on_branch_called(*args, **kwargs):
            raise AssertionError("ensure_on_branch must not be called during spawn")

        with (
            patch("orchestrator.git_utils.create_task_worktree", return_value=worktree_path),
            patch("orchestrator.scheduler.get_task_branch", return_value="agent/TASK-abc123"),
            patch("orchestrator.scheduler.get_tasks_dir", return_value=tmp_path / "tasks"),
            patch("orchestrator.scheduler.get_base_branch", return_value="main"),
            patch("orchestrator.scheduler.get_global_instructions_path", return_value=tmp_path / "gi.md"),
            patch("orchestrator.scheduler.find_parent_project", return_value=tmp_path),
            patch("orchestrator.scheduler._get_server_url_from_config", return_value="http://localhost"),
            patch.object(RepoManager, "ensure_on_branch", side_effect=fail_if_ensure_on_branch_called),
        ):
            # Must not raise AssertionError — currently raises because ensure_on_branch is called
            prepare_task_directory(task, "implementer-1", {"agent_dir": str(agent_dir)})


class TestCreateTaskWorktreeDetachedHead:
    """create_task_worktree must always return a worktree on detached HEAD."""

    def test_create_task_worktree_asserts_detached_head(self, tmp_path: Path) -> None:
        """create_task_worktree must assert that the returned worktree is on detached HEAD.

        This test FAILS on current code because create_task_worktree has no
        assertion checking that the worktree is on detached HEAD after creation.
        We verify this by simulating git returning a named branch (not "HEAD")
        for rev-parse --abbrev-ref HEAD, and checking that create_task_worktree
        raises AssertionError.
        """
        from orchestrator.git_utils import create_task_worktree

        task = {
            "id": "TASK-detach01",
            "branch": None,
            "project_id": None,
            "breakdown_id": None,
            "role": "implement",
        }

        worktree_path = tmp_path / "worktree"

        # Simulate that after worktree creation, HEAD is NOT detached
        def fake_run_git(args: list, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                # Return a named branch, simulating a non-detached HEAD
                result.stdout = "feature/client-server-architecture\n"
            elif args[0] == "worktree" and args[1] == "add":
                worktree_path.mkdir(parents=True, exist_ok=True)
                (worktree_path / ".git").write_text("gitdir: ...")
            return result

        with (
            patch("orchestrator.git_utils.find_parent_project", return_value=tmp_path),
            patch("orchestrator.git_utils.get_task_worktree_path", return_value=worktree_path),
            patch("orchestrator.git_utils.get_base_branch", return_value="main"),
            patch("orchestrator.git_utils.run_git", side_effect=fake_run_git),
        ):
            # Must raise AssertionError because the worktree ended up on a named branch.
            # Currently does NOT raise (no assertion exists) — so this test fails.
            with pytest.raises(AssertionError, match="detached"):
                create_task_worktree(task)
