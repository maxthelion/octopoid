"""Tests for task message history fetching and formatting in the scheduler."""

from unittest.mock import MagicMock, patch

import pytest

from orchestrator.scheduler import _fetch_task_messages, _format_message_thread


class TestFormatMessageThread:
    """Test _format_message_thread formatting logic."""

    def test_empty_messages_returns_empty_string(self):
        """No messages → empty string (so template renders cleanly)."""
        result = _format_message_thread([])
        assert result == ""

    def test_single_rejection_formats_correctly(self):
        """A rejection message shows event, author and reason."""
        messages = [
            {
                "id": 1,
                "task_id": "abc123",
                "event": "rejected",
                "author": "gatekeeper",
                "content": "Tests are not passing",
                "created_at": "2025-01-15T10:30:00Z",
            }
        ]
        result = _format_message_thread(messages)

        assert "## Message History" in result
        assert "rejected" in result.lower() or "Rejection" in result
        assert "gatekeeper" in result
        assert "Tests are not passing" in result

    def test_rejection_without_content_still_renders(self):
        """A rejection with no content (no reason) doesn't crash."""
        messages = [
            {
                "id": 1,
                "task_id": "abc123",
                "event": "rejected",
                "author": "gatekeeper",
                "content": None,
                "created_at": "2025-01-15T10:30:00Z",
            }
        ]
        result = _format_message_thread(messages)
        assert "## Message History" in result

    def test_multiple_events_in_order(self):
        """Multiple messages appear in the formatted output."""
        messages = [
            {
                "id": 1,
                "event": "claimed",
                "author": "implementer-1",
                "content": None,
                "created_at": "2025-01-15T09:00:00Z",
            },
            {
                "id": 2,
                "event": "rejected",
                "author": "gatekeeper",
                "content": "Missing error handling",
                "created_at": "2025-01-15T10:00:00Z",
            },
        ]
        result = _format_message_thread(messages)
        assert "## Message History" in result
        assert "Missing error handling" in result

    def test_submitted_event_renders(self):
        """Submitted events appear in the thread."""
        messages = [
            {
                "id": 1,
                "event": "submitted",
                "author": "implementer-1",
                "content": None,
                "created_at": "2025-01-15T09:00:00Z",
            }
        ]
        result = _format_message_thread(messages)
        assert "## Message History" in result
        assert "submitted" in result.lower() or "Submitted" in result

    def test_timestamp_formatting(self):
        """ISO timestamps are truncated for readability."""
        messages = [
            {
                "id": 1,
                "event": "rejected",
                "author": "gatekeeper",
                "content": "Something went wrong",
                "created_at": "2025-01-15T10:30:45.000Z",
            }
        ]
        result = _format_message_thread(messages)
        # Full ISO string should be shortened
        assert "2025-01-15 10:30" in result

    def test_no_header_for_empty_list(self):
        """Empty list produces no header section."""
        result = _format_message_thread([])
        assert "## Message History" not in result
        assert result == ""


class TestFetchTaskMessages:
    """Test _fetch_task_messages error handling."""

    def test_returns_messages_on_success(self):
        """Returns messages from SDK when call succeeds."""
        mock_sdk = MagicMock()
        mock_sdk.messages.list.return_value = [
            {"id": 1, "event": "rejected", "author": "gatekeeper", "content": "Feedback", "created_at": "2025-01-01"}
        ]

        with patch("orchestrator.scheduler.queue_utils") as mock_queue_utils:
            mock_queue_utils.get_sdk.return_value = mock_sdk
            result = _fetch_task_messages("task-123")

        assert len(result) == 1
        assert result[0]["event"] == "rejected"
        mock_sdk.messages.list.assert_called_once_with("task-123")

    def test_returns_empty_on_sdk_error(self):
        """Returns empty list when SDK call fails (graceful degradation)."""
        with patch("orchestrator.scheduler.queue_utils") as mock_queue_utils:
            mock_queue_utils.get_sdk.side_effect = RuntimeError("SDK not available")
            result = _fetch_task_messages("task-123")

        assert result == []

    def test_returns_empty_on_http_error(self):
        """Returns empty list when messages endpoint is not found (server not deployed)."""
        mock_sdk = MagicMock()
        mock_sdk.messages.list.side_effect = Exception("404 Not Found")

        with patch("orchestrator.scheduler.queue_utils") as mock_queue_utils:
            mock_queue_utils.get_sdk.return_value = mock_sdk
            result = _fetch_task_messages("task-123")

        assert result == []

    def test_returns_empty_list_for_no_messages(self):
        """Returns empty list when task has no history."""
        mock_sdk = MagicMock()
        mock_sdk.messages.list.return_value = []

        with patch("orchestrator.scheduler.queue_utils") as mock_queue_utils:
            mock_queue_utils.get_sdk.return_value = mock_sdk
            result = _fetch_task_messages("task-123")

        assert result == []
