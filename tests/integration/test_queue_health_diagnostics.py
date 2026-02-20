"""Integration tests for queue health diagnostics.

These tests verify that the scheduler health-check functions correctly
detect and handle stuck/stale tasks using a real test server.

The key function under test is `check_and_requeue_expired_leases()` from
the scheduler, which scans claimed tasks for expired leases and requeues
them back to incoming.

Architecture note: `check_queue_health()` (which detects file-DB mismatches,
orphan files, and zombie claims) relies on local filesystem/DB access rather
than the API, so it cannot be tested here. Those diagnostics are covered in
tests/test_queue_diagnostics.py.

The server API does not expose an endpoint to set `lease_expires_at` to an
arbitrary past date, so these tests use time-shifting (patching `datetime.now`
in the scheduler module) to simulate expired leases. The server sets a 5-minute
claim lease; advancing the clock by 7 minutes makes all current leases appear
expired.

Note: The `scoped_sdk` fixture cannot be used here because the current test
server does not filter `tasks.list()` or `tasks.claim()` by scope. The `sdk` +
`clean_tasks` pattern (which deletes all tasks before/after each test) is used
instead to provide real isolation.
"""

from datetime import datetime as _datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import orchestrator.scheduler as _sched_module
from orchestrator.scheduler import check_and_requeue_expired_leases


_TEST_BRANCH = "main"
# Server claim sets a 5-minute lease; advance past this to expire all leases.
_LEASE_DURATION_MINUTES = 5
_TIME_SHIFT_MINUTES = _LEASE_DURATION_MINUTES + 2  # 7 min: just past any lease


def _run_health_check(sdk) -> None:
    """Run the expired-lease health check at the current time.

    Claimed tasks with a future lease are NOT requeued. Use this for
    "healthy queue" assertions.
    """
    with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
        check_and_requeue_expired_leases()


def _run_health_check_time_shifted(
    sdk, minutes_ahead: int = _TIME_SHIFT_MINUTES
) -> None:
    """Run the health check with time shifted forward so server leases appear expired.

    The server sets a 5-minute claim lease. Shifting time forward by more than
    5 minutes makes every current lease appear expired to the scheduler.
    """
    future_now = _datetime.now(timezone.utc) + timedelta(minutes=minutes_ahead)

    with patch.object(_sched_module, "datetime") as mock_dt:
        mock_dt.now.return_value = future_now
        # Keep fromisoformat working for timestamp parsing inside the function
        mock_dt.fromisoformat = _datetime.fromisoformat

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
            check_and_requeue_expired_leases()


def _create_task(sdk, task_id: str, title: str, role: str = "implement") -> dict:
    """Create a test task with all required fields."""
    return sdk.tasks.create(
        id=task_id,
        file_path=f"/tmp/{task_id}.md",
        title=title,
        role=role,
        branch=_TEST_BRANCH,
    )


class TestStuckClaimedTaskDetected:
    """Test 1: A stuck claimed task (expired lease) is detected and requeued."""

    def test_expired_lease_task_is_requeued_to_incoming(
        self, sdk, orchestrator_id, clean_tasks
    ) -> None:
        """A claimed task whose lease has expired must be requeued to 'incoming'.

        Simulates a stuck task by advancing the health-check clock past the
        claim lease duration. The function must detect the expired lease and
        return the task to the incoming queue.
        """
        # 1. Create a task
        task = _create_task(sdk, "stuck-001", "Stuck Task")
        assert task["queue"] == "incoming"

        # 2. Claim it — the server sets lease_expires_at = now + 5 min
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="stuck-agent",
            role_filter="implement",
        )
        assert claimed is not None, "Task should be claimable"
        assert claimed["id"] == "stuck-001"
        assert claimed["queue"] == "claimed"
        assert claimed["lease_expires_at"], "Claim should set a lease"
        task_id = claimed["id"]

        # 3. Run health check with time advanced past the lease expiry
        _run_health_check_time_shifted(sdk)

        # 4. Verify the task was requeued to incoming
        result = sdk.tasks.get(task_id)
        assert result is not None
        assert result["queue"] == "incoming", (
            f"Stuck task should be requeued to 'incoming', got '{result['queue']}'"
        )
        # claimed_by must be cleared on requeue
        assert not result.get("claimed_by"), (
            "Requeued task should have no claimed_by"
        )

    def test_recently_claimed_task_detected_after_lease_expiry(
        self, sdk, orchestrator_id, clean_tasks
    ) -> None:
        """A task whose lease expired 3 minutes ago should still be requeued."""
        _create_task(sdk, "stuck-002", "Another Stuck Task")
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="stuck-agent-2",
            role_filter="implement",
        )
        assert claimed is not None
        assert claimed["id"] == "stuck-002"
        task_id = claimed["id"]

        # Advance time by 8 minutes (3 min past the 5-min lease)
        _run_health_check_time_shifted(sdk, minutes_ahead=8)

        result = sdk.tasks.get(task_id)
        assert result["queue"] == "incoming", (
            "Task with expired lease (8 min > 5 min lease) should be requeued"
        )


class TestHealthyQueueReturnsCleanReport:
    """Test 2: A healthy queue (recent claims, normal states) is left untouched."""

    def test_task_with_valid_lease_is_not_requeued(
        self, sdk, orchestrator_id, clean_tasks
    ) -> None:
        """Tasks with valid (non-expired) leases must not be requeued.

        Runs the health check at the CURRENT time (no time shift). The
        recently-claimed task's lease is still in the future, so it stays claimed.
        """
        # Create an unclaimed task
        _create_task(sdk, "healthy-incoming", "Healthy Incoming Task")

        # Create and claim a task (lease = now + 5 min, still valid)
        _create_task(sdk, "healthy-claimed", "Healthy Claimed Task")
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="healthy-agent",
            role_filter="implement",
        )
        assert claimed is not None
        # Health check only checks claimed queue; first claim returns healthy-claimed
        # (healthy-incoming is already in incoming, so claim picks one at random)
        claimed_id = claimed["id"]
        assert claimed["lease_expires_at"], "Claim should set a future lease"

        # Run health check at current time (no time shift) — all leases valid
        _run_health_check(sdk)

        # The claimed task should still be claimed
        still_claimed = sdk.tasks.get(claimed_id)
        assert still_claimed["queue"] == "claimed", (
            "Task with valid future lease should remain in 'claimed'"
        )

    def test_unclaimed_tasks_are_never_touched(
        self, sdk, clean_tasks
    ) -> None:
        """Tasks that have never been claimed have no lease and are ignored.

        The health check only processes tasks in the 'claimed' queue.
        Tasks in 'incoming' are never scanned, even with extreme time shift.
        """
        for i in range(3):
            _create_task(sdk, f"fresh-{i:03d}", f"Fresh Unclaimed Task {i}")

        # Run health check with extreme time shift — incoming tasks still safe
        _run_health_check_time_shifted(sdk, minutes_ahead=60)

        for i in range(3):
            task = sdk.tasks.get(f"fresh-{i:03d}")
            assert task["queue"] == "incoming", (
                f"Unclaimed task fresh-{i:03d} should not be touched by health check"
            )


class TestMultipleIssuesDetectedSimultaneously:
    """Test 3: Multiple stuck tasks are all detected and requeued in one pass."""

    def test_all_expired_lease_tasks_requeued_in_one_pass(
        self, sdk, orchestrator_id, clean_tasks
    ) -> None:
        """All tasks with expired leases are requeued in a single health check run.

        Creates 3 stuck tasks (all claimed, all with expired leases once time is
        shifted) and 3 unclaimed tasks (no lease). Verifies the health check
        fixes ALL stuck tasks at once without touching unclaimed ones.

        Note: Orphaned-PID and zombie-claim detection requires file-system/DB
        access and is not testable via the API. See tests/test_queue_diagnostics.py
        for those scenarios.
        """
        # Create 3 tasks to be claimed (and thus get expirable leases)
        for i in range(3):
            _create_task(sdk, f"multi-stuck-{i:03d}", f"Stuck Task {i}")

        # Claim all 3 in sequence
        stuck_ids = []
        for i in range(3):
            claimed = sdk.tasks.claim(
                orchestrator_id=orchestrator_id,
                agent_name=f"stuck-agent-{i}",
                role_filter="implement",
            )
            if claimed:
                stuck_ids.append(claimed["id"])

        assert len(stuck_ids) == 3, (
            f"Expected 3 claimable tasks, got {len(stuck_ids)}"
        )
        assert set(stuck_ids) == {"multi-stuck-000", "multi-stuck-001", "multi-stuck-002"}, (
            f"Unexpected task IDs: {stuck_ids}"
        )

        # Create 3 unclaimed tasks — health check should not touch them
        for i in range(3):
            _create_task(sdk, f"multi-safe-{i:03d}", f"Safe Unclaimed Task {i}")

        # Run health check with time shifted past all lease expiries
        _run_health_check_time_shifted(sdk)

        # All 3 stuck tasks must be requeued to incoming
        for task_id in stuck_ids:
            result = sdk.tasks.get(task_id)
            assert result["queue"] == "incoming", (
                f"Stuck task {task_id} should be in 'incoming', got '{result['queue']}'"
            )
            assert not result.get("claimed_by"), (
                f"Requeued task {task_id} should have no claimed_by"
            )

        # All 3 unclaimed tasks must remain in incoming (untouched)
        for i in range(3):
            task = sdk.tasks.get(f"multi-safe-{i:03d}")
            assert task["queue"] == "incoming", (
                f"Unclaimed task multi-safe-{i:03d} should not be touched"
            )
