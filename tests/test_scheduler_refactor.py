"""Tests for scheduler refactor: guard functions, evaluation chain, and spawn strategies.

This test suite verifies the scheduler's pipeline architecture introduced in the refactor.
Tests cover:
- AgentContext dataclass
- All guard functions (enabled, pool_capacity, interval, backpressure, pre_check)
- evaluate_agent guard chain
- get_spawn_strategy dispatch
- run_housekeeping fault isolation

The pool model replaces named agent instances with blueprint-based ephemeral spawning.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

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
    guard_pool_capacity,
    guard_pre_check,
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


class TestGuardPoolCapacity:
    """Test guard_pool_capacity function."""

    @patch("orchestrator.scheduler.count_running_instances")
    def test_at_capacity_returns_false(self, mock_count, tmp_path):
        """Test that a blueprint at max_instances is blocked."""
        mock_count.return_value = 3

        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"blueprint_name": "implementer", "max_instances": 3},
            agent_name="implementer",
            role="implement",
            interval=60,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_pool_capacity(ctx)

        assert proceed is False
        assert "at capacity" in reason
        assert "3/3" in reason

    @patch("orchestrator.scheduler.count_running_instances")
    def test_below_capacity_returns_true(self, mock_count, tmp_path):
        """Test that a blueprint below max_instances passes."""
        mock_count.return_value = 1

        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"blueprint_name": "implementer", "max_instances": 3},
            agent_name="implementer",
            role="implement",
            interval=60,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_pool_capacity(ctx)

        assert proceed is True
        assert reason == ""

    @patch("orchestrator.scheduler.count_running_instances")
    def test_zero_instances_returns_true(self, mock_count, tmp_path):
        """Test that a blueprint with no running instances passes."""
        mock_count.return_value = 0

        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"blueprint_name": "implementer", "max_instances": 1},
            agent_name="implementer",
            role="implement",
            interval=60,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_pool_capacity(ctx)

        assert proceed is True
        assert reason == ""

    @patch("orchestrator.scheduler.count_running_instances")
    def test_defaults_max_instances_to_1(self, mock_count, tmp_path):
        """Test that max_instances defaults to 1 when not specified."""
        mock_count.return_value = 1

        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"blueprint_name": "implementer"},  # No max_instances
            agent_name="implementer",
            role="implement",
            interval=60,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_pool_capacity(ctx)

        assert proceed is False
        assert "at capacity" in reason

    @patch("orchestrator.scheduler.count_running_instances")
    def test_uses_agent_name_as_fallback_blueprint_name(self, mock_count, tmp_path):
        """Test that agent_name is used when blueprint_name is not in config."""
        mock_count.return_value = 0

        state_path = tmp_path / "state.json"
        ctx = AgentContext(
            agent_config={"max_instances": 2},  # No blueprint_name
            agent_name="implementer",
            role="implement",
            interval=60,
            state=AgentState(),
            state_path=state_path,
        )

        proceed, reason = guard_pool_capacity(ctx)

        assert proceed is True
        mock_count.assert_called_once_with("implementer")


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

    @patch("orchestrator.scheduler.check_backpressure_for_role")
    @patch("orchestrator.scheduler.save_state")
    def test_backpressure_blocked_returns_false(self, mock_save_state, mock_check, tmp_path):
        """Test that a blocked role returns False and updates state."""
        mock_check.return_value = (False, "queue full")

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

        proceed, reason = guard_backpressure(ctx)

        assert proceed is False
        assert "backpressure: queue full" in reason
        mock_check.assert_called_once_with("implement")

        # Verify state was updated with blocked reason
        assert ctx.state.extra["blocked_reason"] == "queue full"
        assert "blocked_at" in ctx.state.extra
        mock_save_state.assert_called_once()

    @patch("orchestrator.scheduler.check_backpressure_for_role")
    @patch("orchestrator.scheduler.save_state")
    def test_backpressure_clear_returns_true(self, mock_save_state, mock_check, tmp_path):
        """Test that an unblocked role returns True and clears blocked_reason."""
        mock_check.return_value = (True, "")

        state_path = tmp_path / "state.json"
        state = AgentState()
        # Pre-populate with previous block
        state.extra["blocked_reason"] = "old reason"
        state.extra["blocked_at"] = "2024-01-01T00:00:00"

        ctx = AgentContext(
            agent_config={},
            agent_name="test-agent",
            role="implement",
            interval=300,
            state=state,
            state_path=state_path,
        )

        proceed, reason = guard_backpressure(ctx)

        assert proceed is True
        assert reason == ""

        # Verify blocked state was cleared
        assert "blocked_reason" not in ctx.state.extra
        assert "blocked_at" not in ctx.state.extra


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
# AGENT_GUARDS composition tests
# =============================================================================


class TestAgentGuardsComposition:
    """Test that AGENT_GUARDS contains the expected guards."""

    def test_guard_pool_capacity_in_agent_guards(self):
        """guard_pool_capacity should be in AGENT_GUARDS (pool model)."""
        assert guard_pool_capacity in AGENT_GUARDS

    def test_guard_not_running_not_in_agent_guards(self):
        """guard_not_running should NOT be in AGENT_GUARDS (replaced by pool model)."""
        assert guard_not_running not in AGENT_GUARDS

    def test_guard_enabled_in_agent_guards(self):
        """guard_enabled should be in AGENT_GUARDS."""
        assert guard_enabled in AGENT_GUARDS

    def test_guard_interval_in_agent_guards(self):
        """guard_interval should be in AGENT_GUARDS."""
        assert guard_interval in AGENT_GUARDS

    def test_guard_backpressure_in_agent_guards(self):
        """guard_backpressure should be in AGENT_GUARDS."""
        assert guard_backpressure in AGENT_GUARDS

    def test_guard_pre_check_in_agent_guards(self):
        """guard_pre_check should be in AGENT_GUARDS."""
        assert guard_pre_check in AGENT_GUARDS


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
