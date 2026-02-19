"""Tests for check_and_requeue_expired_leases housekeeping job."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from orchestrator.scheduler import check_and_requeue_expired_leases


def _make_task(task_id: str, lease_expires_at: str | None) -> dict:
    """Helper to build a minimal task dict."""
    return {"id": task_id, "lease_expires_at": lease_expires_at}


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

    def _run(self, tasks: list[dict]) -> MagicMock:
        """Run the function with a mocked SDK and return the mock SDK."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.list.return_value = tasks

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=mock_sdk):
            check_and_requeue_expired_leases()

        return mock_sdk

    def test_expired_task_is_requeued(self) -> None:
        """A task whose lease_expires_at is in the past must be moved to incoming."""
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

    def test_none_claimed_queue_does_nothing(self) -> None:
        """When sdk.tasks.list returns None, no updates are made and no exception raised."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.list.return_value = None

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

    def test_claimed_list_is_queried(self) -> None:
        """The function must query the 'claimed' queue."""
        sdk = self._run([])
        sdk.tasks.list.assert_called_once_with(queue="claimed")
