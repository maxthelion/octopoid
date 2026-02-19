"""Tests for poll-based scheduler refactor.

Covers:
- guard_backpressure using pre-fetched queue_counts (no API calls)
- can_claim_task() with and without pre-fetched queue_counts
- scheduler_state.json per-job interval management
- _register_orchestrator skipping when orchestrator_registered=True
- process_orchestrator_hooks using pre-fetched provisional_tasks
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.backpressure import can_claim_task
from orchestrator.scheduler import (
    AgentContext,
    HOUSEKEEPING_JOB_INTERVALS,
    _register_orchestrator,
    guard_backpressure,
    is_job_due,
    load_scheduler_state,
    process_orchestrator_hooks,
    record_job_run,
    save_scheduler_state,
)
from orchestrator.state_utils import AgentState


# =============================================================================
# can_claim_task with pre-fetched queue_counts
# =============================================================================


class TestCanClaimTaskWithQueueCounts:
    """can_claim_task should use pre-fetched counts when provided."""

    def test_uses_queue_counts_no_api_call(self):
        """When queue_counts is provided, no API calls are made."""
        queue_counts = {"incoming": 3, "claimed": 0, "provisional": 0}
        with patch("orchestrator.backpressure.count_queue") as mock_count:
            result, reason = can_claim_task(queue_counts=queue_counts)
        mock_count.assert_not_called()
        assert result is True

    def test_queue_counts_empty_incoming_blocked(self):
        """Pre-fetched incoming=0 blocks claiming."""
        queue_counts = {"incoming": 0, "claimed": 0, "provisional": 0}
        result, reason = can_claim_task(queue_counts=queue_counts)
        assert result is False
        assert "No tasks" in reason

    def test_queue_counts_claimed_at_limit_blocked(self):
        """Pre-fetched claimed at limit blocks claiming."""
        from orchestrator.config import get_queue_limits
        limits = get_queue_limits()
        queue_counts = {
            "incoming": 5,
            "claimed": limits["max_claimed"],
            "provisional": 0,
        }
        result, reason = can_claim_task(queue_counts=queue_counts)
        assert result is False
        assert "claimed" in reason.lower()

    def test_queue_counts_provisional_at_limit_blocked(self):
        """Pre-fetched provisional at limit blocks claiming."""
        from orchestrator.config import get_queue_limits
        limits = get_queue_limits()
        queue_counts = {
            "incoming": 5,
            "claimed": 0,
            "provisional": limits["max_provisional"],
        }
        result, reason = can_claim_task(queue_counts=queue_counts)
        assert result is False
        assert "provisional" in reason.lower()

    def test_none_falls_back_to_api(self):
        """When queue_counts is None, falls back to individual API calls."""
        with patch("orchestrator.backpressure.count_queue", return_value=2) as mock_count:
            result, reason = can_claim_task(queue_counts=None)
        # count_queue should be called multiple times (incoming, claimed, provisional)
        assert mock_count.call_count >= 1


# =============================================================================
# guard_backpressure using pre-fetched queue_counts via AgentContext
# =============================================================================


class TestGuardBackpressureWithQueueCounts:
    """guard_backpressure should use ctx.queue_counts when provided."""

    def _make_ctx(self, tmp_path: Path, claim_from: str, queue_counts: dict | None) -> AgentContext:
        return AgentContext(
            agent_config={"claim_from": claim_from},
            agent_name="implementer-1",
            role="implement",
            interval=300,
            state=AgentState(),
            state_path=tmp_path / "state.json",
            queue_counts=queue_counts,
        )

    def test_incoming_uses_queue_counts_no_api_call(self, tmp_path):
        """When queue_counts is in ctx, no API calls are made for incoming queue."""
        ctx = self._make_ctx(
            tmp_path, "incoming",
            queue_counts={"incoming": 3, "claimed": 0, "provisional": 0},
        )
        with patch("orchestrator.backpressure.count_queue") as mock_count:
            proceed, reason = guard_backpressure(ctx)
        mock_count.assert_not_called()
        assert proceed is True

    def test_incoming_queue_counts_empty_returns_false(self, tmp_path):
        """With pre-fetched incoming=0, guard blocks without API call."""
        ctx = self._make_ctx(
            tmp_path, "incoming",
            queue_counts={"incoming": 0, "claimed": 0, "provisional": 0},
        )
        with patch("orchestrator.backpressure.count_queue") as mock_count:
            proceed, reason = guard_backpressure(ctx)
        mock_count.assert_not_called()
        assert proceed is False
        assert "no_tasks" in reason

    def test_provisional_uses_queue_counts(self, tmp_path):
        """Non-incoming queue uses queue_counts when available."""
        ctx = self._make_ctx(
            tmp_path, "provisional",
            queue_counts={"incoming": 2, "claimed": 0, "provisional": 1},
        )
        with patch("orchestrator.backpressure.count_queue") as mock_count:
            proceed, reason = guard_backpressure(ctx)
        mock_count.assert_not_called()
        assert proceed is True

    def test_provisional_empty_via_queue_counts(self, tmp_path):
        """Non-incoming queue blocks when count is 0 from queue_counts."""
        ctx = self._make_ctx(
            tmp_path, "provisional",
            queue_counts={"incoming": 0, "claimed": 0, "provisional": 0},
        )
        with patch("orchestrator.backpressure.count_queue") as mock_count:
            proceed, reason = guard_backpressure(ctx)
        mock_count.assert_not_called()
        assert proceed is False
        assert "no_provisional_tasks" in reason

    def test_none_queue_counts_falls_back_to_api(self, tmp_path):
        """When ctx.queue_counts is None, API is called (backwards compat)."""
        ctx = self._make_ctx(tmp_path, "incoming", queue_counts=None)
        with patch("orchestrator.backpressure.count_queue", return_value=0):
            proceed, reason = guard_backpressure(ctx)
        assert proceed is False


# =============================================================================
# Per-job interval management
# =============================================================================


class TestJobIntervalManagement:
    """Tests for load/save scheduler_state and is_job_due / record_job_run."""

    def test_load_scheduler_state_missing_file(self, tmp_path):
        """Returns empty structure when file doesn't exist."""
        with patch("orchestrator.scheduler.get_scheduler_state_path", return_value=tmp_path / "missing.json"):
            state = load_scheduler_state()
        assert state == {"jobs": {}}

    def test_load_scheduler_state_reads_file(self, tmp_path):
        """Returns stored state when file exists."""
        state_file = tmp_path / "scheduler_state.json"
        state_file.write_text(json.dumps({"jobs": {"check_and_update_finished_agents": "2026-01-01T00:00:00"}}))
        with patch("orchestrator.scheduler.get_scheduler_state_path", return_value=state_file):
            state = load_scheduler_state()
        assert "check_and_update_finished_agents" in state["jobs"]

    def test_save_and_reload_roundtrip(self, tmp_path):
        """save_scheduler_state + load_scheduler_state roundtrip preserves data."""
        state_file = tmp_path / "scheduler_state.json"
        original = {"jobs": {"_register_orchestrator": "2026-02-19T12:00:00"}}
        with patch("orchestrator.scheduler.get_scheduler_state_path", return_value=state_file):
            save_scheduler_state(original)
            loaded = load_scheduler_state()
        assert loaded == original

    def test_is_job_due_never_run(self):
        """Job with no last_run is always due."""
        state = {"jobs": {}}
        assert is_job_due(state, "some_job", 300) is True

    def test_is_job_due_recently_run_not_due(self):
        """Job run 5s ago with 300s interval is not due."""
        recent = (datetime.now() - timedelta(seconds=5)).isoformat()
        state = {"jobs": {"_register_orchestrator": recent}}
        assert is_job_due(state, "_register_orchestrator", 300) is False

    def test_is_job_due_long_ago_is_due(self):
        """Job run 400s ago with 300s interval is due."""
        old = (datetime.now() - timedelta(seconds=400)).isoformat()
        state = {"jobs": {"_register_orchestrator": old}}
        assert is_job_due(state, "_register_orchestrator", 300) is True

    def test_record_job_run_sets_timestamp(self):
        """record_job_run stores a timestamp close to now."""
        state: dict = {"jobs": {}}
        before = datetime.now()
        record_job_run(state, "my_job")
        after = datetime.now()
        ts = datetime.fromisoformat(state["jobs"]["my_job"])
        assert before <= ts <= after

    def test_record_job_run_initializes_jobs_key(self):
        """record_job_run creates 'jobs' key if missing."""
        state: dict = {}
        record_job_run(state, "my_job")
        assert "jobs" in state
        assert "my_job" in state["jobs"]

    def test_housekeeping_intervals_defined(self):
        """All expected jobs have interval entries."""
        expected = {
            "check_and_update_finished_agents",
            "_register_orchestrator",
            "check_and_requeue_expired_leases",
            "process_orchestrator_hooks",
            "_check_queue_health_throttled",
            "agent_evaluation_loop",
        }
        assert expected == set(HOUSEKEEPING_JOB_INTERVALS.keys())

    def test_check_finished_agents_interval_is_10s(self):
        """check_and_update_finished_agents must run every 10s."""
        assert HOUSEKEEPING_JOB_INTERVALS["check_and_update_finished_agents"] == 10

    def test_agent_evaluation_loop_interval_is_60s(self):
        """Agent evaluation loop interval must be 60s."""
        assert HOUSEKEEPING_JOB_INTERVALS["agent_evaluation_loop"] == 60


# =============================================================================
# _register_orchestrator skips when already registered
# =============================================================================


class TestRegisterOrchestratorSkip:
    """_register_orchestrator should skip POST when orchestrator_registered=True."""

    def test_skips_post_when_already_registered(self):
        """No POST request when orchestrator_registered=True."""
        mock_sdk = MagicMock()
        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.scheduler.queue_utils.get_orchestrator_id", return_value="test-orch"),
        ):
            _register_orchestrator(orchestrator_registered=True)
        mock_sdk._request.assert_not_called()

    def test_sends_post_when_not_registered(self):
        """POST request sent when orchestrator_registered=False."""
        mock_sdk = MagicMock()
        with (
            patch("orchestrator.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.queue_utils.get_orchestrator_id", return_value="test-orch"),
        ):
            _register_orchestrator(orchestrator_registered=False)
        mock_sdk._request.assert_called_once()
        call_args = mock_sdk._request.call_args
        assert call_args.args[0] == "POST"
        assert "register" in call_args.args[1]

    def test_sends_post_by_default(self):
        """Default behaviour (no orchestrator_registered arg) sends POST."""
        mock_sdk = MagicMock()
        with (
            patch("orchestrator.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.queue_utils.get_orchestrator_id", return_value="test-orch"),
        ):
            _register_orchestrator()
        mock_sdk._request.assert_called_once()


# =============================================================================
# process_orchestrator_hooks uses pre-fetched provisional_tasks
# =============================================================================


class TestProcessOrchestratorHooksWithPreFetched:
    """process_orchestrator_hooks should use pre-fetched provisional_tasks when provided."""

    def test_skips_list_call_when_tasks_provided(self):
        """When provisional_tasks is provided, sdk.tasks.list is not called."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.list.return_value = []
        mock_hook_manager = MagicMock()
        mock_hook_manager.get_pending_hooks.return_value = []

        with (
            patch("orchestrator.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.scheduler.HookManager", return_value=mock_hook_manager),
        ):
            process_orchestrator_hooks(provisional_tasks=[])

        mock_sdk.tasks.list.assert_not_called()

    def test_uses_api_when_tasks_is_none(self):
        """When provisional_tasks is None, sdk.tasks.list is called."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.list.return_value = []
        mock_hook_manager = MagicMock()
        mock_hook_manager.get_pending_hooks.return_value = []

        with (
            patch("orchestrator.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.scheduler.HookManager", return_value=mock_hook_manager),
        ):
            process_orchestrator_hooks(provisional_tasks=None)

        mock_sdk.tasks.list.assert_called_once_with(queue="provisional")

    def test_processes_provided_tasks(self):
        """Tasks from provisional_tasks are passed to the hook manager."""
        task = {"id": "TASK-abc", "hooks": "[]", "pr_number": None}
        mock_sdk = MagicMock()
        mock_hook_manager = MagicMock()
        mock_hook_manager.get_pending_hooks.return_value = []

        with (
            patch("orchestrator.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.scheduler.HookManager", return_value=mock_hook_manager),
        ):
            process_orchestrator_hooks(provisional_tasks=[task])

        mock_hook_manager.get_pending_hooks.assert_called_once_with(task, hook_type="orchestrator")
