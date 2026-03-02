"""Tests for poll-based scheduler refactor.

Covers:
- guard_backpressure using pre-fetched queue_counts (no API calls)
- can_claim_task() with and without pre-fetched queue_counts
- scheduler_state.json per-job interval management
- _register_orchestrator skipping when orchestrator_registered=True
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from octopoid.backpressure import can_claim_task
from octopoid.jobs import load_jobs_yaml
from octopoid.scheduler import (
    AgentContext,
    _register_orchestrator,
    guard_backpressure,
    is_job_due,
    load_scheduler_state,
    record_job_run,
    save_scheduler_state,
)
from octopoid.state_utils import AgentState


def _get_job_intervals() -> dict[str, int]:
    """Derive job intervals from .octopoid/jobs.yaml for test assertions."""
    return {j["name"]: j["interval"] for j in load_jobs_yaml() if "name" in j and "interval" in j}


# =============================================================================
# can_claim_task with pre-fetched queue_counts
# =============================================================================


class TestCanClaimTaskWithQueueCounts:
    """can_claim_task should use pre-fetched counts when provided."""

    def test_uses_queue_counts_no_api_call(self):
        """When queue_counts is provided, only claimed count is re-fetched for scope isolation."""
        queue_counts = {"incoming": 3, "claimed": 0, "provisional": 0}
        with patch("octopoid.backpressure.count_queue", return_value=0) as mock_count:
            result, reason = can_claim_task(queue_counts=queue_counts)
        # claimed is always re-fetched via count_queue for scope filtering (GH-227)
        mock_count.assert_called_once_with("claimed")
        assert result is True

    def test_queue_counts_empty_incoming_blocked(self):
        """Pre-fetched incoming=0 blocks claiming."""
        queue_counts = {"incoming": 0, "claimed": 0, "provisional": 0}
        result, reason = can_claim_task(queue_counts=queue_counts)
        assert result is False
        assert "No tasks" in reason

    def test_queue_counts_claimed_at_limit_blocked(self):
        """Scope-filtered claimed at limit blocks claiming.

        count_queue("claimed") is always called even when queue_counts is provided,
        to ensure scope isolation (GH-227). The queue_counts claimed value is ignored.
        """
        from octopoid.config import get_queue_limits
        limits = get_queue_limits()
        queue_counts = {
            "incoming": 5,
            "claimed": 0,  # poll says 0, but...
            "provisional": 0,
        }
        # ...scope-filtered count is at the limit
        with patch("octopoid.backpressure.count_queue", return_value=limits["max_claimed"]):
            result, reason = can_claim_task(queue_counts=queue_counts)
        assert result is False
        assert "claimed" in reason.lower()

    def test_queue_counts_provisional_at_limit_blocked(self):
        """Pre-fetched provisional at limit blocks claiming."""
        from octopoid.config import get_queue_limits
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
        with patch("octopoid.backpressure.count_queue", return_value=2) as mock_count:
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
        """When queue_counts is in ctx, only claimed count is re-fetched for scope isolation."""
        ctx = self._make_ctx(
            tmp_path, "incoming",
            queue_counts={"incoming": 3, "claimed": 0, "provisional": 0},
        )
        with patch("octopoid.backpressure.count_queue", return_value=0) as mock_count:
            proceed, reason = guard_backpressure(ctx)
        # claimed is always re-fetched for scope filtering (GH-227); incoming uses poll data
        mock_count.assert_called_once_with("claimed")
        assert proceed is True

    def test_incoming_queue_counts_empty_returns_false(self, tmp_path):
        """With pre-fetched incoming=0, guard blocks without API call."""
        ctx = self._make_ctx(
            tmp_path, "incoming",
            queue_counts={"incoming": 0, "claimed": 0, "provisional": 0},
        )
        with patch("octopoid.backpressure.count_queue") as mock_count:
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
        with patch("octopoid.backpressure.count_queue") as mock_count:
            proceed, reason = guard_backpressure(ctx)
        mock_count.assert_not_called()
        assert proceed is True

    def test_provisional_empty_via_queue_counts(self, tmp_path):
        """Non-incoming queue blocks when count is 0 from queue_counts."""
        ctx = self._make_ctx(
            tmp_path, "provisional",
            queue_counts={"incoming": 0, "claimed": 0, "provisional": 0},
        )
        with patch("octopoid.backpressure.count_queue") as mock_count:
            proceed, reason = guard_backpressure(ctx)
        mock_count.assert_not_called()
        assert proceed is False
        assert "no_provisional_tasks" in reason

    def test_none_queue_counts_falls_back_to_api(self, tmp_path):
        """When ctx.queue_counts is None, API is called (backwards compat)."""
        ctx = self._make_ctx(tmp_path, "incoming", queue_counts=None)
        with patch("octopoid.backpressure.count_queue", return_value=0):
            proceed, reason = guard_backpressure(ctx)
        assert proceed is False


# =============================================================================
# Per-job interval management
# =============================================================================


class TestJobIntervalManagement:
    """Tests for load/save scheduler_state and is_job_due / record_job_run."""

    def test_load_scheduler_state_missing_file(self, tmp_path):
        """Returns empty structure when file doesn't exist."""
        with patch("octopoid.scheduler.get_scheduler_state_path", return_value=tmp_path / "missing.json"):
            state = load_scheduler_state()
        assert state == {"jobs": {}}

    def test_load_scheduler_state_reads_file(self, tmp_path):
        """Returns stored state when file exists."""
        state_file = tmp_path / "scheduler_state.json"
        state_file.write_text(json.dumps({"jobs": {"check_and_update_finished_agents": "2026-01-01T00:00:00"}}))
        with patch("octopoid.scheduler.get_scheduler_state_path", return_value=state_file):
            state = load_scheduler_state()
        assert "check_and_update_finished_agents" in state["jobs"]

    def test_save_and_reload_roundtrip(self, tmp_path):
        """save_scheduler_state + load_scheduler_state roundtrip preserves data."""
        state_file = tmp_path / "scheduler_state.json"
        original = {"jobs": {"_register_orchestrator": "2026-02-19T12:00:00"}}
        with patch("octopoid.scheduler.get_scheduler_state_path", return_value=state_file):
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
        """All expected jobs have interval entries in jobs.yaml."""
        expected = {
            "check_and_update_finished_agents",
            "_register_orchestrator",
            "check_and_requeue_expired_leases",
            "check_project_completion",
            "_check_queue_health_throttled",
            "agent_evaluation_loop",
            "sweep_stale_resources",
            "poll_github_issues",
            "send_heartbeat",
            "codebase_analyst",
            "dispatch_action_messages",
            "testing_analyst",
            "architecture_analyst",
            "renew_active_leases",
        }
        intervals = _get_job_intervals()
        assert expected == set(intervals.keys())

    def test_check_finished_agents_interval_is_10s(self):
        """check_and_update_finished_agents must run every 10s."""
        intervals = _get_job_intervals()
        assert intervals["check_and_update_finished_agents"] == 10

    def test_agent_evaluation_loop_interval_is_60s(self):
        """Agent evaluation loop interval must be 60s."""
        intervals = _get_job_intervals()
        assert intervals["agent_evaluation_loop"] == 60


# =============================================================================
# _register_orchestrator skips when already registered
# =============================================================================


class TestRegisterOrchestratorSkip:
    """_register_orchestrator should skip POST when orchestrator_registered=True."""

    def test_skips_post_when_already_registered(self):
        """No POST request when orchestrator_registered=True."""
        mock_sdk = MagicMock()
        with (
            patch("octopoid.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("octopoid.scheduler.queue_utils.get_orchestrator_id", return_value="test-orch"),
        ):
            _register_orchestrator(orchestrator_registered=True)
        mock_sdk._request.assert_not_called()

    def test_sends_post_when_not_registered(self):
        """POST request sent when orchestrator_registered=False."""
        mock_sdk = MagicMock()
        with (
            patch("octopoid.queue_utils.get_sdk", return_value=mock_sdk),
            patch("octopoid.queue_utils.get_orchestrator_id", return_value="test-orch"),
        ):
            _register_orchestrator(orchestrator_registered=False)
        # _request is called at least once for registration; flow sync may add more calls
        assert mock_sdk._request.call_count >= 1
        first_call = mock_sdk._request.call_args_list[0]
        assert first_call.args[0] == "POST"
        assert "register" in first_call.args[1]

    def test_sends_post_by_default(self):
        """Default behaviour (no orchestrator_registered arg) sends POST."""
        mock_sdk = MagicMock()
        with (
            patch("octopoid.queue_utils.get_sdk", return_value=mock_sdk),
            patch("octopoid.queue_utils.get_orchestrator_id", return_value="test-orch"),
        ):
            _register_orchestrator()
        # _request is called at least once for registration; flow sync may add more calls
        assert mock_sdk._request.call_count >= 1
