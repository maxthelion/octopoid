"""Tests for check_and_requeue_expired_leases and _requeue_task."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from orchestrator.scheduler import _requeue_task, check_and_requeue_expired_leases


def _make_task(
    task_id: str,
    lease_expires_at: str | None,
    claimed_by: str | None = "agent-1",
) -> dict:
    """Helper to build a minimal task dict."""
    return {"id": task_id, "lease_expires_at": lease_expires_at, "claimed_by": claimed_by}


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
    ) -> MagicMock:
        """Run the function with a mocked SDK and return the mock SDK.

        Args:
            claimed_tasks: Tasks returned for sdk.tasks.list(queue="claimed").
            provisional_tasks: Tasks returned for sdk.tasks.list(queue="provisional").
                Defaults to [] (no provisional tasks with active claims).
        """
        mock_sdk = MagicMock()

        def _list(queue: str | None = None) -> list[dict]:
            if queue == "claimed":
                return claimed_tasks
            if queue == "provisional":
                return provisional_tasks or []
            return []

        mock_sdk.tasks.list.side_effect = _list

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk):
            check_and_requeue_expired_leases()

        return mock_sdk

    def test_expired_task_is_requeued(self) -> None:
        """A claimed task whose lease_expires_at is in the past is moved to incoming."""
        task = _make_task("TASK-expired", _expired())
        sdk = self._run([task])

        sdk.tasks.update.assert_called_once_with(
            "TASK-expired",
            queue="incoming",
            claimed_by=None,
            lease_expires_at=None,
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

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk):
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

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk):
            check_and_requeue_expired_leases()  # must not raise

    def test_z_suffix_timestamp_is_parsed(self) -> None:
        """Timestamps ending with 'Z' (UTC) must be correctly parsed and compared."""
        # Build an expired timestamp with 'Z' suffix
        expired_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        # Format with 'Z' suffix (not '+00:00')
        lease = expired_dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

        task = _make_task("TASK-z-suffix", lease)
        sdk = self._run([task])

        sdk.tasks.update.assert_called_once_with(
            "TASK-z-suffix",
            queue="incoming",
            claimed_by=None,
            lease_expires_at=None,
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


# ===========================================================================
# _requeue_task unit tests
# ===========================================================================


class TestRequeuTask:
    """_requeue_task must return a task to the correct source queue."""

    def _run_requeue(self, task_id: str, source_queue: str = "incoming") -> MagicMock:
        """Run _requeue_task with a mocked SDK and return the mock."""
        mock_sdk = MagicMock()
        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk):
            _requeue_task(task_id, source_queue=source_queue)
        return mock_sdk

    def test_defaults_to_incoming(self) -> None:
        """With no source_queue argument, task is returned to 'incoming'."""
        mock_sdk = MagicMock()
        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk):
            _requeue_task("TASK-abc")

        mock_sdk.tasks.update.assert_called_once_with(
            "TASK-abc",
            queue="incoming",
            claimed_by=None,
            lease_expires_at=None,
        )

    def test_incoming_source_queue(self) -> None:
        """Explicitly passing source_queue='incoming' returns task to incoming."""
        sdk = self._run_requeue("TASK-impl", source_queue="incoming")

        sdk.tasks.update.assert_called_once_with(
            "TASK-impl",
            queue="incoming",
            claimed_by=None,
            lease_expires_at=None,
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

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk):
            _requeue_task("TASK-fail")  # must not raise
