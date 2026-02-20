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

from orchestrator.scheduler import (
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
        "orchestrator.scheduler.datetime",
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

        # Run mock agent with failure outcome — writes result.json without
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

        # Process result — transitions task from 'claimed' to 'failed'.
        handle_agent_result(task_id, "mock-implementer", task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "failed", (
            f"Precondition: expected failed after agent result, got {task['queue']!r}"
        )

        # Run lease monitor with real time — task is not in 'claimed', must be a no-op.
        # Also run with mocked future time to be sure even a "stale" entry is ignored.
        with _advance_time_to_future():
            check_and_requeue_expired_leases()

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "failed", (
            f"Expected task to remain in 'failed' after lease monitor, "
            f"got {task['queue']!r}"
        )
        assert task["queue"] != "incoming", (
            "Lease monitor must not requeue a task that already left 'claimed'"
        )
