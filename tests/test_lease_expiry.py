"""Tests for check_and_requeue_expired_leases and _requeue_task."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from orchestrator.scheduler import _requeue_task, check_and_requeue_expired_leases


def _make_task(
    task_id: str,
    lease_expires_at: str | None,
    claimed_by: str | None = "agent-1",
    attempt_count: int = 0,
) -> dict:
    """Helper to build a minimal task dict."""
    return {
        "id": task_id,
        "lease_expires_at": lease_expires_at,
        "claimed_by": claimed_by,
        "attempt_count": attempt_count,
    }


def _expired(minutes_ago: int = 10) -> str:
    """Return an ISO timestamp that is already past."""
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.isoformat()


def _future(minutes_ahead: int = 10) -> str:
    """Return an ISO timestamp that is in the future."""
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes_ahead)
    return dt.isoformat()


class TestCheckAndRequeueExpiredLeases:
    """check_and_requeue_expired_leases must requeue tasks whose lease has expired."""

    def _run(
        self,
        claimed_tasks: list[dict],
        provisional_tasks: list[dict] | None = None,
        threshold: int = 3,
    ) -> MagicMock:
        """Run the function with a mocked SDK and return the mock SDK.

        Args:
            claimed_tasks: Tasks returned for sdk.tasks.list(queue="claimed").
            provisional_tasks: Tasks returned for sdk.tasks.list(queue="provisional").
                Defaults to [] (no provisional tasks with active claims).
            threshold: Circuit breaker threshold (default 3).
        """
        mock_sdk = MagicMock()

        def _list(queue: str | None = None) -> list[dict]:
            if queue == "claimed":
                return claimed_tasks
            if queue == "provisional":
                return provisional_tasks or []
            return []

        mock_sdk.tasks.list.side_effect = _list

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.scheduler._get_circuit_breaker_threshold", return_value=threshold),
        ):
            check_and_requeue_expired_leases()

        return mock_sdk

    def test_expired_task_is_requeued(self) -> None:
        """A claimed task whose lease_expires_at is in the past is moved to incoming."""
        task = _make_task("TASK-expired", _expired(), attempt_count=0)
        sdk = self._run([task])

        sdk.tasks.update.assert_called_once_with(
            "TASK-expired",
            queue="incoming",
            claimed_by=None,
            lease_expires_at=None,
            attempt_count=1,
        )

    def test_valid_lease_task_is_not_requeued(self) -> None:
        """A task whose lease_expires_at is in the future must not be touched."""
        task = _make_task("TASK-valid", _future())
        sdk = self._run([task])

        sdk.tasks.update.assert_not_called()

    def test_task_without_lease_is_skipped(self) -> None:
        """A task with no lease_expires_at field must be skipped."""
        task = _make_task("TASK-no-lease", None)
        sdk = self._run([task])

        sdk.tasks.update.assert_not_called()

    def test_only_expired_tasks_are_requeued_among_mixed_list(self) -> None:
        """Only expired tasks are requeued; valid-lease and no-lease tasks are skipped."""
        tasks = [
            _make_task("TASK-expired-1", _expired(5)),
            _make_task("TASK-valid-1", _future(5)),
            _make_task("TASK-no-lease-1", None),
            _make_task("TASK-expired-2", _expired(60)),
        ]
        sdk = self._run(tasks)

        assert sdk.tasks.update.call_count == 2
        updated_ids = {c.args[0] for c in sdk.tasks.update.call_args_list}
        assert updated_ids == {"TASK-expired-1", "TASK-expired-2"}

    def test_empty_claimed_queue_does_nothing(self) -> None:
        """When claimed queue is empty, no updates are made."""
        sdk = self._run([])

        sdk.tasks.update.assert_not_called()

    def test_none_queues_do_nothing(self) -> None:
        """When sdk.tasks.list returns None for all queues, no exception raised."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.list.side_effect = lambda queue=None: None

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.scheduler._get_circuit_breaker_threshold", return_value=3),
        ):
            check_and_requeue_expired_leases()  # must not raise

        mock_sdk.tasks.update.assert_not_called()

    def test_invalid_lease_format_is_skipped(self) -> None:
        """A task with an unparseable lease_expires_at must be silently skipped."""
        task = _make_task("TASK-bad-format", "not-a-date")
        sdk = self._run([task])

        sdk.tasks.update.assert_not_called()

    def test_sdk_exception_does_not_propagate(self) -> None:
        """If the SDK raises, the function must swallow the exception."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.list.side_effect = RuntimeError("network error")

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.scheduler._get_circuit_breaker_threshold", return_value=3),
        ):
            check_and_requeue_expired_leases()  # must not raise

    def test_z_suffix_timestamp_is_parsed(self) -> None:
        """Timestamps ending with 'Z' (UTC) must be correctly parsed and compared."""
        # Build an expired timestamp with 'Z' suffix
        expired_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        # Format with 'Z' suffix (not '+00:00')
        lease = expired_dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

        task = _make_task("TASK-z-suffix", lease, attempt_count=0)
        sdk = self._run([task])

        sdk.tasks.update.assert_called_once_with(
            "TASK-z-suffix",
            queue="incoming",
            claimed_by=None,
            lease_expires_at=None,
            attempt_count=1,
        )

    def test_both_queues_are_queried(self) -> None:
        """The function must query both 'claimed' and 'provisional' queues."""
        sdk = self._run([])
        queried = {c.kwargs.get("queue") for c in sdk.tasks.list.call_args_list}
        assert "claimed" in queried
        assert "provisional" in queried

    # ------------------------------------------------------------------
    # Provisional queue: gatekeeper review tasks
    # ------------------------------------------------------------------

    def test_expired_provisional_task_stays_in_provisional(self) -> None:
        """An actively-claimed provisional task with an expired lease is unclaimed in-place.

        Gatekeeper uses claim_for_review, so the task stays in 'provisional'.
        On lease expiry we clear claimed_by/lease_expires_at but do NOT move
        it to 'incoming'.
        """
        task = _make_task("TASK-prov-expired", _expired(), claimed_by="gatekeeper-1")
        sdk = self._run(claimed_tasks=[], provisional_tasks=[task])

        sdk.tasks.update.assert_called_once_with(
            "TASK-prov-expired",
            queue="provisional",
            claimed_by=None,
            lease_expires_at=None,
        )

    def test_unclaimed_provisional_task_is_skipped(self) -> None:
        """A provisional task with no claimed_by must not be touched.

        Unclaimed provisional tasks (waiting for reviewer) have no active claim
        and therefore no lease to expire.
        """
        task = _make_task("TASK-prov-unclaimed", _expired(), claimed_by=None)
        sdk = self._run(claimed_tasks=[], provisional_tasks=[task])

        sdk.tasks.update.assert_not_called()

    def test_provisional_task_valid_lease_not_requeued(self) -> None:
        """A claimed provisional task with a valid lease must not be touched."""
        task = _make_task("TASK-prov-valid", _future(), claimed_by="gatekeeper-1")
        sdk = self._run(claimed_tasks=[], provisional_tasks=[task])

        sdk.tasks.update.assert_not_called()

    def test_expired_tasks_from_both_queues_are_handled_correctly(self) -> None:
        """Expired tasks in both 'claimed' and 'provisional' are requeued to correct queues."""
        claimed_task = _make_task("TASK-claimed-expired", _expired(), claimed_by="impl-1")
        prov_task = _make_task("TASK-prov-expired", _expired(), claimed_by="gate-1")
        sdk = self._run(claimed_tasks=[claimed_task], provisional_tasks=[prov_task])

        assert sdk.tasks.update.call_count == 2
        calls = {c.args[0]: c.kwargs["queue"] for c in sdk.tasks.update.call_args_list}
        assert calls["TASK-claimed-expired"] == "incoming"
        assert calls["TASK-prov-expired"] == "provisional"

    # ------------------------------------------------------------------
    # Circuit breaker tests
    # ------------------------------------------------------------------

    def test_circuit_breaker_trips_on_threshold_reached(self) -> None:
        """When attempt_count + 1 >= threshold, move task to failed instead of incoming."""
        # attempt_count=2, threshold=3 → new_attempt_count=3 >= 3 → trip
        task = _make_task("TASK-cb", _expired(), attempt_count=2)
        sdk = self._run([task], threshold=3)

        sdk.tasks.update.assert_called_once()
        call_kwargs = sdk.tasks.update.call_args
        assert call_kwargs.args[0] == "TASK-cb"
        assert call_kwargs.kwargs["queue"] == "failed"
        assert call_kwargs.kwargs["attempt_count"] == 3

    def test_circuit_breaker_does_not_trip_below_threshold(self) -> None:
        """When attempt_count + 1 < threshold, task is returned to incoming."""
        # attempt_count=1, threshold=3 → new_attempt_count=2 < 3 → requeue
        task = _make_task("TASK-no-cb", _expired(), attempt_count=1)
        sdk = self._run([task], threshold=3)

        sdk.tasks.update.assert_called_once()
        call_kwargs = sdk.tasks.update.call_args
        assert call_kwargs.args[0] == "TASK-no-cb"
        assert call_kwargs.kwargs["queue"] == "incoming"
        assert call_kwargs.kwargs["attempt_count"] == 2

    def test_circuit_breaker_increments_attempt_count_on_requeue(self) -> None:
        """Each requeue increments attempt_count on the server."""
        task = _make_task("TASK-incr", _expired(), attempt_count=0)
        sdk = self._run([task], threshold=3)

        sdk.tasks.update.assert_called_once()
        assert sdk.tasks.update.call_args.kwargs["attempt_count"] == 1

    def test_circuit_breaker_no_attempt_increment_for_provisional(self) -> None:
        """Provisional lease expiry never increments attempt_count."""
        task = _make_task("TASK-prov-cb", _expired(), claimed_by="gate-1", attempt_count=99)
        sdk = self._run(claimed_tasks=[], provisional_tasks=[task], threshold=3)

        sdk.tasks.update.assert_called_once_with(
            "TASK-prov-cb",
            queue="provisional",
            claimed_by=None,
            lease_expires_at=None,
        )

    def test_circuit_breaker_threshold_configurable(self) -> None:
        """Circuit breaker threshold is respected when set to a custom value."""
        # With threshold=1, the very first expiry should trip the breaker
        task = _make_task("TASK-custom-threshold", _expired(), attempt_count=0)
        sdk = self._run([task], threshold=1)

        sdk.tasks.update.assert_called_once()
        assert sdk.tasks.update.call_args.kwargs["queue"] == "failed"


# ===========================================================================
# _requeue_task unit tests
# ===========================================================================


class TestRequeuTask:
    """_requeue_task must return a task to the correct source queue."""

    def _run_requeue(
        self,
        task_id: str,
        source_queue: str = "incoming",
        task: dict | None = None,
        threshold: int = 3,
    ) -> MagicMock:
        """Run _requeue_task with a mocked SDK and return the mock."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.get.return_value = task or {"attempt_count": 0}

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.scheduler._get_circuit_breaker_threshold", return_value=threshold),
        ):
            _requeue_task(task_id, source_queue=source_queue, task=task)

        return mock_sdk

    def test_defaults_to_incoming(self) -> None:
        """With no source_queue argument, task is returned to 'incoming'."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.get.return_value = {"attempt_count": 0}

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.scheduler._get_circuit_breaker_threshold", return_value=3),
        ):
            _requeue_task("TASK-abc", task={"attempt_count": 0})

        mock_sdk.tasks.update.assert_called_once_with(
            "TASK-abc",
            queue="incoming",
            claimed_by=None,
            lease_expires_at=None,
            attempt_count=1,
        )

    def test_incoming_source_queue(self) -> None:
        """Explicitly passing source_queue='incoming' returns task to incoming."""
        sdk = self._run_requeue("TASK-impl", source_queue="incoming", task={"attempt_count": 0})

        sdk.tasks.update.assert_called_once_with(
            "TASK-impl",
            queue="incoming",
            claimed_by=None,
            lease_expires_at=None,
            attempt_count=1,
        )

    def test_provisional_source_queue(self) -> None:
        """source_queue='provisional' returns gatekeeper task to provisional."""
        sdk = self._run_requeue("TASK-gate", source_queue="provisional")

        sdk.tasks.update.assert_called_once_with(
            "TASK-gate",
            queue="provisional",
            claimed_by=None,
            lease_expires_at=None,
        )

    def test_sdk_exception_is_swallowed(self) -> None:
        """SDK failure must not propagate out of _requeue_task."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.update.side_effect = RuntimeError("network error")
        mock_sdk.tasks.get.return_value = {"attempt_count": 0}

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.scheduler._get_circuit_breaker_threshold", return_value=3),
        ):
            _requeue_task("TASK-fail", task={"attempt_count": 0})  # must not raise

    # ------------------------------------------------------------------
    # Circuit breaker tests for _requeue_task
    # ------------------------------------------------------------------

    def test_circuit_breaker_trips_on_spawn_failure(self) -> None:
        """When attempt_count reaches threshold, move to failed instead of incoming."""
        # attempt_count=2, threshold=3 → new=3 >= 3 → trip
        sdk = self._run_requeue(
            "TASK-spawn-cb",
            source_queue="incoming",
            task={"attempt_count": 2},
            threshold=3,
        )

        sdk.tasks.update.assert_called_once()
        call_kwargs = sdk.tasks.update.call_args.kwargs
        assert call_kwargs["queue"] == "failed"
        assert call_kwargs["attempt_count"] == 3

    def test_circuit_breaker_does_not_trip_below_threshold(self) -> None:
        """Below threshold, task goes back to incoming."""
        sdk = self._run_requeue(
            "TASK-spawn-ok",
            source_queue="incoming",
            task={"attempt_count": 1},
            threshold=3,
        )

        call_kwargs = sdk.tasks.update.call_args.kwargs
        assert call_kwargs["queue"] == "incoming"
        assert call_kwargs["attempt_count"] == 2

    def test_circuit_breaker_fetches_task_if_not_provided(self) -> None:
        """When no task dict is provided, fetches task from server."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.get.return_value = {"attempt_count": 0}

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.scheduler._get_circuit_breaker_threshold", return_value=3),
        ):
            _requeue_task("TASK-fetch", source_queue="incoming")

        mock_sdk.tasks.get.assert_called_once_with("TASK-fetch")
        assert mock_sdk.tasks.update.call_args.kwargs["attempt_count"] == 1

    def test_circuit_breaker_not_applied_for_provisional(self) -> None:
        """Provisional requeues never trigger circuit breaker (no attempt_count change)."""
        sdk = self._run_requeue(
            "TASK-prov",
            source_queue="provisional",
            task={"attempt_count": 99},
            threshold=1,
        )

        # Even with threshold=1 and attempt_count=99, provisional should not trip
        sdk.tasks.update.assert_called_once_with(
            "TASK-prov",
            queue="provisional",
            claimed_by=None,
            lease_expires_at=None,
        )
