"""Tests for guard chain composition and spawn strategy routing.

Regression tests to catch regressions like the guard_claim_task deletion incident
(commit d858559) that caused 50+ scheduler ticks with zero successful claims.

Covers:
- AGENT_GUARDS list composition (all required guards present)
- get_spawn_strategy() routing for each spawn mode
- guard_not_running individual behavior
- guard_claim_task individual behavior
- guard_backpressure individual behavior
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.scheduler import (
    AGENT_GUARDS,
    AgentContext,
    get_spawn_strategy,
    guard_backpressure,
    guard_claim_task,
    guard_not_running,
    spawn_implementer,
    spawn_lightweight,
    spawn_worktree,
)
from orchestrator.state_utils import AgentState


# =============================================================================
# Helper
# =============================================================================


def make_ctx(tmp_path: Path, **kwargs) -> AgentContext:
    """Build a minimal AgentContext for testing."""
    defaults = dict(
        agent_config={},
        agent_name="test-agent",
        role="implementer",
        interval=300,
        state=AgentState(),
        state_path=tmp_path / "state.json",
        claimed_task=None,
    )
    defaults.update(kwargs)
    return AgentContext(**defaults)


# =============================================================================
# 1. Guard chain composition
# =============================================================================


class TestAgentGuardsComposition:
    """Assert that AGENT_GUARDS contains all required guards in order.

    Removing a guard from AGENT_GUARDS should fail these tests — that's the
    whole point: catch the d858559-style regression immediately.
    """

    def test_agent_guards_contains_required_guards(self):
        guard_names = [g.__name__ for g in AGENT_GUARDS]
        assert "guard_not_running" in guard_names
        assert "guard_backpressure" in guard_names
        assert "guard_claim_task" in guard_names

    def test_agent_guards_contains_enabled_and_interval(self):
        guard_names = [g.__name__ for g in AGENT_GUARDS]
        assert "guard_enabled" in guard_names
        assert "guard_interval" in guard_names

    def test_agent_guards_order_cheap_before_expensive(self):
        """Cheapest guards (enabled, not_running, interval) must come before
        expensive ones (backpressure, pre_check, claim_task)."""
        guard_names = [g.__name__ for g in AGENT_GUARDS]

        cheap = ["guard_enabled", "guard_not_running", "guard_interval"]
        expensive = ["guard_backpressure", "guard_claim_task"]

        for cheap_guard in cheap:
            for expensive_guard in expensive:
                if cheap_guard in guard_names and expensive_guard in guard_names:
                    assert guard_names.index(cheap_guard) < guard_names.index(
                        expensive_guard
                    ), (
                        f"{cheap_guard} must appear before {expensive_guard} in AGENT_GUARDS"
                    )

    def test_agent_guards_is_a_list(self):
        assert isinstance(AGENT_GUARDS, list)
        assert len(AGENT_GUARDS) >= 3


# =============================================================================
# 2. Spawn strategy selection
# =============================================================================


class TestGetSpawnStrategy:
    """Test that get_spawn_strategy() returns the correct function."""

    def test_scripts_mode_with_claimed_task_returns_spawn_implementer(self, tmp_path):
        ctx = make_ctx(
            tmp_path,
            agent_config={"spawn_mode": "scripts"},
            claimed_task={"id": "TASK-abc123", "title": "Test"},
        )
        assert get_spawn_strategy(ctx) is spawn_implementer

    def test_scripts_mode_without_claimed_task_does_not_return_spawn_implementer(
        self, tmp_path
    ):
        ctx = make_ctx(
            tmp_path,
            agent_config={"spawn_mode": "scripts"},
            claimed_task=None,
        )
        strategy = get_spawn_strategy(ctx)
        assert strategy is not spawn_implementer

    def test_scripts_mode_without_claimed_task_falls_back_to_spawn_worktree(
        self, tmp_path
    ):
        ctx = make_ctx(
            tmp_path,
            agent_config={"spawn_mode": "scripts"},
            claimed_task=None,
        )
        assert get_spawn_strategy(ctx) is spawn_worktree

    def test_lightweight_true_returns_spawn_lightweight(self, tmp_path):
        ctx = make_ctx(
            tmp_path,
            agent_config={"lightweight": True},
        )
        assert get_spawn_strategy(ctx) is spawn_lightweight

    def test_default_mode_returns_spawn_worktree(self, tmp_path):
        ctx = make_ctx(tmp_path, agent_config={})
        assert get_spawn_strategy(ctx) is spawn_worktree

    def test_lightweight_false_returns_spawn_worktree(self, tmp_path):
        ctx = make_ctx(tmp_path, agent_config={"lightweight": False})
        assert get_spawn_strategy(ctx) is spawn_worktree


# =============================================================================
# 3. guard_not_running unit tests
# =============================================================================


class TestGuardNotRunning:
    """Unit tests for guard_not_running."""

    def test_idle_agent_passes(self, tmp_path):
        ctx = make_ctx(tmp_path, state=AgentState(running=False, pid=None))
        proceed, reason = guard_not_running(ctx)
        assert proceed is True
        assert reason == ""

    @patch("orchestrator.scheduler.is_process_running", return_value=True)
    def test_alive_pid_returns_false(self, _mock, tmp_path):
        ctx = make_ctx(tmp_path, state=AgentState(running=True, pid=42))
        proceed, reason = guard_not_running(ctx)
        assert proceed is False
        assert "42" in reason

    @patch("orchestrator.scheduler.is_process_running", return_value=False)
    @patch("orchestrator.scheduler.save_state")
    @patch("orchestrator.scheduler.mark_finished")
    def test_crashed_agent_cleaned_up_and_passes(
        self, mock_mark_finished, mock_save_state, _mock_running, tmp_path
    ):
        """Agent marked running but PID is dead → clean up and allow spawn."""
        finished = AgentState(running=False, pid=None, last_exit_code=1)
        mock_mark_finished.return_value = finished

        ctx = make_ctx(tmp_path, state=AgentState(running=True, pid=99999))
        proceed, reason = guard_not_running(ctx)

        assert proceed is True
        mock_mark_finished.assert_called_once()
        mock_save_state.assert_called_once()


# =============================================================================
# 4. guard_claim_task unit tests
# =============================================================================


class TestGuardClaimTask:
    """Unit tests for guard_claim_task."""

    def test_non_scripts_mode_skips_claim(self, tmp_path):
        """Worktree-mode agents skip claiming and always proceed."""
        ctx = make_ctx(tmp_path, agent_config={"spawn_mode": "worktree"})
        proceed, reason = guard_claim_task(ctx)
        assert proceed is True
        assert reason == ""

    def test_default_mode_skips_claim(self, tmp_path):
        """Agents without spawn_mode config (default worktree) also skip."""
        ctx = make_ctx(tmp_path, agent_config={})
        proceed, reason = guard_claim_task(ctx)
        assert proceed is True

    @patch("orchestrator.scheduler.get_claim_queue_for_role", return_value="incoming")
    @patch("orchestrator.scheduler.claim_and_prepare_task", return_value=None)
    def test_no_available_task_returns_false(self, _mock_claim, _mock_queue, tmp_path):
        ctx = make_ctx(tmp_path, agent_config={"spawn_mode": "scripts"})
        proceed, reason = guard_claim_task(ctx)
        assert proceed is False
        assert reason == "no_task_to_claim"

    @patch("orchestrator.scheduler.get_claim_queue_for_role", return_value="incoming")
    @patch(
        "orchestrator.scheduler.claim_and_prepare_task",
        return_value={"id": "TASK-xyz", "title": "Do work"},
    )
    def test_successful_claim_sets_claimed_task_and_returns_true(
        self, _mock_claim, _mock_queue, tmp_path
    ):
        ctx = make_ctx(tmp_path, agent_config={"spawn_mode": "scripts"})
        proceed, reason = guard_claim_task(ctx)
        assert proceed is True
        assert ctx.claimed_task == {"id": "TASK-xyz", "title": "Do work"}

    @patch("orchestrator.scheduler.get_claim_queue_for_role", return_value="incoming")
    @patch(
        "orchestrator.scheduler.claim_and_prepare_task",
        return_value={"id": "TASK-xyz"},
    )
    def test_claim_from_incoming_uses_role_filter(
        self, mock_claim, _mock_queue, tmp_path
    ):
        """When claiming from 'incoming', role_filter should equal the agent's role."""
        ctx = make_ctx(
            tmp_path,
            agent_config={"spawn_mode": "scripts"},
            role="implementer",
        )
        guard_claim_task(ctx)
        _, call_kwargs = mock_claim.call_args
        assert call_kwargs.get("role_filter") == "implementer"

    @patch(
        "orchestrator.scheduler.get_claim_queue_for_role", return_value="provisional"
    )
    @patch(
        "orchestrator.scheduler.claim_and_prepare_task",
        return_value={"id": "TASK-xyz"},
    )
    def test_claim_from_non_incoming_passes_none_role_filter(
        self, mock_claim, _mock_queue, tmp_path
    ):
        """When claiming from a non-incoming queue, role_filter must be None
        so tasks with different original roles are accepted."""
        ctx = make_ctx(
            tmp_path,
            agent_config={"spawn_mode": "scripts"},
            role="gatekeeper",
        )
        guard_claim_task(ctx)
        _, call_kwargs = mock_claim.call_args
        assert call_kwargs.get("role_filter") is None


# =============================================================================
# 5. guard_backpressure unit tests
# =============================================================================


class TestGuardBackpressure:
    """Unit tests for guard_backpressure."""

    @patch("orchestrator.scheduler.check_backpressure_for_role", return_value=(False, "no_tasks"))
    @patch("orchestrator.scheduler.save_state")
    def test_backpressure_blocks_when_no_tasks(self, _save, _check, tmp_path):
        ctx = make_ctx(tmp_path, state=AgentState())
        proceed, reason = guard_backpressure(ctx)
        assert proceed is False
        assert "no_tasks" in reason
        assert ctx.state.extra.get("blocked_reason") == "no_tasks"

    @patch("orchestrator.scheduler.check_backpressure_for_role", return_value=(True, ""))
    @patch("orchestrator.scheduler.save_state")
    def test_backpressure_passes_when_work_available(self, _save, _check, tmp_path):
        ctx = make_ctx(tmp_path, state=AgentState())
        proceed, reason = guard_backpressure(ctx)
        assert proceed is True
        assert reason == ""

    @patch("orchestrator.scheduler.check_backpressure_for_role", return_value=(True, ""))
    @patch("orchestrator.scheduler.save_state")
    def test_backpressure_clears_previous_blocked_state(self, _save, _check, tmp_path):
        state = AgentState()
        state.extra["blocked_reason"] = "stale"
        state.extra["blocked_at"] = "2024-01-01T00:00:00"
        ctx = make_ctx(tmp_path, state=state)

        guard_backpressure(ctx)

        assert "blocked_reason" not in ctx.state.extra
        assert "blocked_at" not in ctx.state.extra
