"""Integration tests for lease expiry end-to-end recovery.

Tests that check_and_requeue_expired_leases() correctly:
- Requeues tasks whose lease has expired back to 'incoming'
- Leaves tasks alone when they are no longer in 'claimed' (agent finished)

Runs against a real local server (port 9787). No sleeps, no polling.

Lease expiry is simulated by mocking `datetime.now()` inside the scheduler
module to return a far-future timestamp. The task is claimed with a short
lease (lease_duration_seconds=1) so that the mocked "now" is clearly past
the expiry without requiring any actual waiting.

Run with a local server on port 9787:
    cd submodules/server && npx wrangler dev --port 9787
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from octopoid.scheduler import (
    check_and_requeue_expired_leases,
    handle_agent_result,
)

# Re-use module-level helpers and constants from the mock scheduler tests.
from tests.integration.test_scheduler_mock import (
    FAKE_GH_BIN,
    _init_git_repo_basic,
    _make_task_id,
    _run_mock_agent,
)

# A timestamp far enough in the future that any real lease_expires_at will
# appear expired.  Using year 2099 avoids any off-by-one or clock skew issues.
_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _advance_time_to_future():
    """Context manager: make datetime.now() in the scheduler return _FAR_FUTURE.

    Only orchestrator.scheduler.datetime.now is patched; fromisoformat() is
    preserved so the ISO string parsing in the lease check still works.
    """
    return patch(
        "octopoid.scheduler.datetime",
        **{
            "now.return_value": _FAR_FUTURE,
            "fromisoformat.side_effect": datetime.fromisoformat,
        },
    )


# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_gh_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prepend the fake gh binary directory to PATH for every test."""
    current_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{FAKE_GH_BIN}:{current_path}")


# ---------------------------------------------------------------------------
# Lease expiry tests
# ---------------------------------------------------------------------------


class TestLeaseExpiry:
    """Tests for lease expiry detection and recovery."""

    def test_expired_lease_requeues_to_incoming(
        self,
        scoped_sdk,
        orchestrator_id: str,
        clean_tasks,
    ) -> None:
        """Claimed task with an expired lease is requeued to incoming.

        Steps:
        1. Create task and claim it with a 1-second lease so lease_expires_at
           is set by the server.
        2. Mock datetime.now() in the scheduler to return a far-future time,
           making the lease appear expired without any sleep.
        3. Call check_and_requeue_expired_leases() — must detect the expiry
           and move the task back to 'incoming'.
        4. Assert queue is 'incoming'.
        """
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Lease expiry — requeue test",
            role="implement",
            branch="main",
        )
        # Claim with a short lease so the server sets lease_expires_at.
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
            lease_duration_seconds=1,
        )

        # Verify task is in 'claimed' and has lease_expires_at set.
        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "claimed", (
            f"Precondition: expected claimed, got {task['queue']!r}"
        )
        assert task.get("lease_expires_at"), (
            "Precondition: lease_expires_at must be set after claim"
        )

        # Run the lease monitor with mocked time (far future → all leases expired).
        with _advance_time_to_future():
            check_and_requeue_expired_leases()

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "incoming", (
            f"Expected task requeued to 'incoming' after lease expiry, "
            f"got {task['queue']!r}"
        )

    def test_expired_lease_task_is_re_claimable(
        self,
        scoped_sdk,
        orchestrator_id: str,
        clean_tasks,
    ) -> None:
        """After lease expiry and requeue, the task can be claimed again.

        Verifies the full recovery loop:
        claim → lease expires → requeue → re-claim succeeds.
        """
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Lease expiry — re-claimable test",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
            lease_duration_seconds=1,
        )

        # Run monitor with mocked future time to expire the lease.
        with _advance_time_to_future():
            check_and_requeue_expired_leases()

        # Verify requeued to incoming.
        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "incoming", (
            f"Precondition: expected incoming after requeue, got {task['queue']!r}"
        )

        # Claim again — must succeed and return the same task.
        reclaimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
        )
        assert reclaimed is not None, (
            "Task must be re-claimable after lease expiry recovery"
        )
        assert reclaimed["id"] == task_id, (
            f"Expected to reclaim task {task_id!r}, got {reclaimed['id']!r}"
        )

    def test_active_lease_not_requeued(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Agent finishes before lease expires — check_and_requeue_expired_leases is a no-op.

        Steps:
        1. Create task and claim it.
        2. Mock agent writes a failure result (simplest path out of 'claimed'
           that requires no git remote for push_branch).
        3. handle_agent_result() moves the task from 'claimed' to 'failed'.
        4. check_and_requeue_expired_leases() with real time — task is no longer
           in 'claimed', so the monitor must leave it in 'failed'.
        """
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Active lease — no requeue test",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
        )

        # Run mock agent with failure outcome — writes stdout.log without
        # requiring a git remote (failure path skips push_branch).
        worktree = tmp_path / "worktree"
        _init_git_repo_basic(worktree)
        task_dir = tmp_path / "task"

        result = _run_mock_agent(
            worktree,
            task_dir,
            commits=1,
            outcome="failure",
            reason="simulated agent finish",
        )
        assert result.returncode == 0, f"Mock agent failed: {result.stderr}"

        # Process result — transitions task from 'claimed' to 'requires-intervention'
        # (first failure routes through request_intervention, not directly to failed).
        handle_agent_result(task_id, "mock-implementer", task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "requires-intervention", (
            f"Precondition: expected requires-intervention after agent result, got {task['queue']!r}"
        )

        # Run lease monitor with real time — task is not in 'claimed', must be a no-op.
        # Also run with mocked future time to be sure even a "stale" entry is ignored.
        with _advance_time_to_future():
            check_and_requeue_expired_leases()

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "requires-intervention", (
            f"Expected task to remain in 'requires-intervention' after lease monitor, "
            f"got {task['queue']!r}"
        )
        assert task["queue"] != "incoming", (
            "Lease monitor must not requeue a task that already left 'claimed'"
        )


class TestLeaseRecoveryProvisional:
    """Tests for lease expiry recovery in the provisional queue.

    When a gatekeeper claims a task from provisional, the server sets
    claimed_by and lease_expires_at. If the gatekeeper dies or the lease
    expires, the scheduler should clear these fields so the task is
    available for another gatekeeper to claim.
    """

    def _create_provisional_task(self, sdk, orch_id, task_id):
        """Create a task and advance it to provisional."""
        sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title=f"Provisional lease test: {task_id}",
            role="implement",
            priority="P1",
            branch="main",
        )
        sdk.tasks.claim(
            orchestrator_id=orch_id,
            agent_name="test-impl",
            role_filter="implement",
            lease_duration_seconds=3600,
        )
        submitted = sdk.tasks.submit(task_id=task_id, commits_count=1, turns_used=5)
        assert submitted["queue"] == "provisional"
        return submitted

    def test_expired_provisional_lease_is_cleared(
        self,
        scoped_sdk,
        orchestrator_id: str,
        clean_tasks,
    ) -> None:
        """Gatekeeper claims from provisional, lease expires, claim is cleared.

        The task should stay in provisional but with claimed_by and
        lease_expires_at both set to None, so another gatekeeper can claim it.
        """
        task_id = _make_task_id()
        self._create_provisional_task(scoped_sdk, orchestrator_id, task_id)

        # Claim from provisional (gatekeeper) with a short lease
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-gatekeeper",
            queue="provisional",
            lease_duration_seconds=1,
        )
        assert claimed is not None, "Gatekeeper claim from provisional should succeed"
        assert claimed.get("claimed_by") == "test-gatekeeper"
        assert claimed.get("lease_expires_at") is not None

        # Run the lease monitor with mocked far-future time
        with _advance_time_to_future():
            check_and_requeue_expired_leases()

        # Task should stay in provisional but claim fields cleared
        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "provisional", (
            f"Task should stay in provisional, got {task['queue']}"
        )
        assert task.get("claimed_by") is None, (
            f"claimed_by should be cleared, got {task.get('claimed_by')}"
        )
        assert task.get("lease_expires_at") is None, (
            f"lease_expires_at should be cleared, got {task.get('lease_expires_at')}"
        )

    @pytest.mark.xfail(
        reason="Known bug: PATCH /tasks/:id doesn't accept lease_expires_at. "
               "See project-management/tasks/octopoid-server/fix-patch-lease-expires-at.md",
        strict=True,
    )
    def test_stale_lease_without_claimed_by_is_still_cleared(
        self,
        scoped_sdk,
        orchestrator_id: str,
        clean_tasks,
    ) -> None:
        """BUG: claimed_by cleared but lease_expires_at left behind.

        This reproduces the scenario where check_and_update_finished_agents
        (or some other code path) clears claimed_by on a provisional task
        but does NOT clear lease_expires_at. The task ends up with:
          claimed_by=None, lease_expires_at=<expired timestamp>

        check_and_requeue_expired_leases skips provisional tasks where
        claimed_by is falsy (line 1768), so the stale lease is never cleaned.

        This test should FAIL until the bug is fixed.
        """
        task_id = _make_task_id()
        self._create_provisional_task(scoped_sdk, orchestrator_id, task_id)

        # Claim from provisional with short lease
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-gatekeeper",
            queue="provisional",
            lease_duration_seconds=1,
        )
        assert claimed is not None

        # Simulate the inconsistent state: clear claimed_by but leave lease_expires_at.
        # This is what happens when check_and_update_finished_agents processes
        # a dead gatekeeper but only clears claimed_by.
        scoped_sdk.tasks.update(task_id, claimed_by=None)

        # Verify the inconsistent state exists
        task = scoped_sdk.tasks.get(task_id)
        assert task.get("claimed_by") is None, "Precondition: claimed_by should be None"
        assert task.get("lease_expires_at") is not None, (
            "Precondition: lease_expires_at should still be set"
        )

        # Run the lease monitor with mocked far-future time
        with _advance_time_to_future():
            check_and_requeue_expired_leases()

        # BUG: the function skips provisional tasks where claimed_by is None
        # (line 1768), so lease_expires_at is never cleared.
        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "provisional"
        assert task.get("lease_expires_at") is None, (
            f"BUG: lease_expires_at should be cleared even when claimed_by is None, "
            f"but got {task.get('lease_expires_at')}"
        )
