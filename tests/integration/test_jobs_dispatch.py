"""Integration tests for the run_due_jobs dispatch cycle.

Exercises the full dispatch path against a real local server:
  YAML loading → job classification → _run_job → registered job function
  → real server state change.

The test scenario verifies that an expired lease is re-queued to 'incoming'
via run_due_jobs(), not by calling check_and_requeue_expired_leases() directly.
This exercises the complete dispatch chain that runs in production.

Run with a local server on port 9787:
    ./tests/integration/bin/start-test-server.sh
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from octopoid.jobs import run_due_jobs

# A timestamp far enough in the future that any real lease_expires_at will
# appear expired. Using year 2099 avoids any off-by-one or clock skew issues.
_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _advance_time_to_future():
    """Context manager: make datetime.now() in the scheduler return _FAR_FUTURE.

    Only octopoid.scheduler.datetime.now is patched; fromisoformat() is
    preserved so the ISO string parsing in the lease check still works.
    """
    return patch(
        "octopoid.scheduler.datetime",
        **{
            "now.return_value": _FAR_FUTURE,
            "fromisoformat.side_effect": datetime.fromisoformat,
        },
    )


def _make_task_id() -> str:
    return f"DISPATCH-{uuid.uuid4().hex[:8].upper()}"


class TestRunDueJobsDispatch:
    """Integration tests for the run_due_jobs dispatch cycle.

    Tests that run_due_jobs() correctly loads job definitions from YAML,
    classifies them by group, and dispatches each due job through _run_job()
    to the registered handler function, which then makes real server state changes.
    """

    def test_expired_lease_requeued_via_run_due_jobs(
        self,
        scoped_sdk,
        orchestrator_id: str,
        clean_tasks,
    ) -> None:
        """Expired lease is re-queued via the full run_due_jobs dispatch path.

        This test exercises:
          YAML loading → job classification → _run_job
          → check_and_requeue_expired_leases → real server state change.

        Steps:
        1. Create a task and claim it with a 1-second lease so the server
           sets lease_expires_at.
        2. Build a scheduler_state where check_and_requeue_expired_leases is
           overdue (has never run — absent from the state dict).
        3. Patch load_jobs_yaml to return only the check_and_requeue_expired_leases
           job definition, preventing side effects from other jobs running.
        4. Patch _fetch_poll_data to return an empty dict (avoids needing a
           registered orchestrator for the poll endpoint).
        5. Patch datetime.now() in the scheduler to return far-future, making
           the lease appear expired.
        6. Call run_due_jobs(scheduler_state).
        7. Assert the task queue is 'incoming' — the full dispatch path worked.
        8. Assert scheduler_state was updated with a run timestamp for the job.
        """
        # -- Setup: create a task and claim it --------------------------------
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="run_due_jobs dispatch — lease expiry test",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
            lease_duration_seconds=1,
        )

        # Verify precondition: task is in 'claimed' and has a lease.
        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "claimed", (
            f"Precondition: expected 'claimed', got {task['queue']!r}"
        )
        assert task.get("lease_expires_at"), (
            "Precondition: lease_expires_at must be set after claim"
        )

        # -- Build scheduler_state: only our target job is overdue ------------
        # The job is absent from the dict, so is_job_due returns True for it.
        scheduler_state: dict = {"jobs": {}}

        # -- Dispatch with targeted mocks -------------------------------------
        # Load only the target job so other jobs don't run as side effects.
        target_job_def = {
            "name": "check_and_requeue_expired_leases",
            "interval": 60,
            "type": "script",
            "group": "remote",
        }
        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=[target_job_def]),
            patch("octopoid.scheduler._fetch_poll_data", return_value={}),
            _advance_time_to_future(),
        ):
            run_due_jobs(scheduler_state)

        # -- Assert: task is back in incoming ---------------------------------
        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "incoming", (
            f"Expected task requeued to 'incoming' after run_due_jobs dispatch, "
            f"got {task['queue']!r}"
        )

        # -- Assert: scheduler_state records the job run ----------------------
        assert "check_and_requeue_expired_leases" in scheduler_state.get("jobs", {}), (
            "run_due_jobs must record job run timestamp in scheduler_state"
        )
        last_run = scheduler_state["jobs"]["check_and_requeue_expired_leases"]
        assert last_run, "Last-run timestamp must be non-empty"

    def test_run_due_jobs_skips_jobs_with_recent_run(
        self,
        scoped_sdk,
        orchestrator_id: str,
        clean_tasks,
    ) -> None:
        """Jobs with a recent last-run timestamp are not dispatched.

        Verifies that is_job_due correctly prevents re-running a job whose
        interval has not yet elapsed. Creates a task, claims it, marks
        check_and_requeue_expired_leases as having run just now, then calls
        run_due_jobs() and asserts the task stays in 'claimed'.
        """
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="run_due_jobs skip — recent run test",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
            lease_duration_seconds=3600,
        )

        # Verify precondition.
        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "claimed"

        # Mark the job as having run just now — interval is 60s so it won't be due.
        recent_time = datetime.now(timezone.utc).isoformat()
        scheduler_state: dict = {
            "jobs": {
                "check_and_requeue_expired_leases": recent_time,
            }
        }

        target_job_def = {
            "name": "check_and_requeue_expired_leases",
            "interval": 60,
            "type": "script",
            "group": "remote",
        }
        with (
            patch("octopoid.jobs.load_jobs_yaml", return_value=[target_job_def]),
            patch("octopoid.scheduler._fetch_poll_data", return_value={}),
        ):
            run_due_jobs(scheduler_state)

        # Task must still be in 'claimed' — job was skipped as not due.
        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "claimed", (
            f"Expected task to remain in 'claimed' when job not due, "
            f"got {task['queue']!r}"
        )
