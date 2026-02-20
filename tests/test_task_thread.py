"""Tests for the task message thread system."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch


def make_threads_dir(tmp_path: Path) -> Path:
    threads_dir = tmp_path / "shared" / "threads"
    threads_dir.mkdir(parents=True, exist_ok=True)
    return threads_dir


class TestPostMessage:
    """Tests for post_message."""

    def test_post_creates_jsonl_file(self, tmp_path):
        threads_dir = make_threads_dir(tmp_path)

        with patch("orchestrator.task_thread.get_threads_dir", return_value=threads_dir):
            from orchestrator.task_thread import post_message, get_thread

            post_message("abc123", role="rejection", content="Tests fail", author="gatekeeper")

            thread_file = threads_dir / "TASK-abc123.jsonl"
            assert thread_file.exists()

            lines = thread_file.read_text().strip().splitlines()
            assert len(lines) == 1
            msg = json.loads(lines[0])
            assert msg["role"] == "rejection"
            assert msg["content"] == "Tests fail"
            assert msg["author"] == "gatekeeper"
            assert "timestamp" in msg

    def test_post_appends_multiple_messages(self, tmp_path):
        threads_dir = make_threads_dir(tmp_path)

        with patch("orchestrator.task_thread.get_threads_dir", return_value=threads_dir):
            from orchestrator.task_thread import post_message

            post_message("abc123", role="rejection", content="First rejection", author="gk-1")
            post_message("abc123", role="rejection", content="Second rejection", author="gk-2")

            thread_file = threads_dir / "TASK-abc123.jsonl"
            lines = thread_file.read_text().strip().splitlines()
            assert len(lines) == 2

            msg1 = json.loads(lines[0])
            msg2 = json.loads(lines[1])
            assert msg1["content"] == "First rejection"
            assert msg2["content"] == "Second rejection"

    def test_post_without_author(self, tmp_path):
        threads_dir = make_threads_dir(tmp_path)

        with patch("orchestrator.task_thread.get_threads_dir", return_value=threads_dir):
            from orchestrator.task_thread import post_message

            post_message("abc123", role="rejection", content="No author")

            thread_file = threads_dir / "TASK-abc123.jsonl"
            lines = thread_file.read_text().strip().splitlines()
            msg = json.loads(lines[0])
            assert "author" not in msg

    def test_post_different_tasks_separate_files(self, tmp_path):
        threads_dir = make_threads_dir(tmp_path)

        with patch("orchestrator.task_thread.get_threads_dir", return_value=threads_dir):
            from orchestrator.task_thread import post_message

            post_message("task1", role="rejection", content="Task 1 rejection")
            post_message("task2", role="rejection", content="Task 2 rejection")

            assert (threads_dir / "TASK-task1.jsonl").exists()
            assert (threads_dir / "TASK-task2.jsonl").exists()


class TestGetThread:
    """Tests for get_thread."""

    def test_get_empty_thread_for_new_task(self, tmp_path):
        threads_dir = make_threads_dir(tmp_path)

        with patch("orchestrator.task_thread.get_threads_dir", return_value=threads_dir):
            from orchestrator.task_thread import get_thread

            messages = get_thread("nonexistent")
            assert messages == []

    def test_get_thread_returns_messages_in_order(self, tmp_path):
        threads_dir = make_threads_dir(tmp_path)

        with patch("orchestrator.task_thread.get_threads_dir", return_value=threads_dir):
            from orchestrator.task_thread import post_message, get_thread

            post_message("abc", role="rejection", content="First")
            post_message("abc", role="rejection", content="Second")
            post_message("abc", role="rejection", content="Third")

            messages = get_thread("abc")
            assert len(messages) == 3
            assert messages[0]["content"] == "First"
            assert messages[1]["content"] == "Second"
            assert messages[2]["content"] == "Third"

    def test_get_thread_skips_malformed_lines(self, tmp_path):
        threads_dir = make_threads_dir(tmp_path)
        thread_file = threads_dir / "TASK-abc.jsonl"
        thread_file.write_text('{"role":"rejection","content":"ok"}\nnot-valid-json\n{"role":"rejection","content":"also ok"}\n')

        with patch("orchestrator.task_thread.get_threads_dir", return_value=threads_dir):
            from orchestrator.task_thread import get_thread

            messages = get_thread("abc")
            assert len(messages) == 2
            assert messages[0]["content"] == "ok"
            assert messages[1]["content"] == "also ok"


class TestFormatThreadForPrompt:
    """Tests for format_thread_for_prompt."""

    def test_empty_messages_returns_empty_string(self):
        from orchestrator.task_thread import format_thread_for_prompt

        result = format_thread_for_prompt([])
        assert result == ""

    def test_non_rejection_messages_ignored(self):
        from orchestrator.task_thread import format_thread_for_prompt

        messages = [{"role": "info", "content": "some info"}]
        result = format_thread_for_prompt(messages)
        assert result == ""

    def test_rejection_messages_formatted(self):
        from orchestrator.task_thread import format_thread_for_prompt

        messages = [
            {
                "role": "rejection",
                "content": "Tests fail: 3 failures",
                "author": "gatekeeper",
                "timestamp": "2026-02-20T10:00:00",
            }
        ]
        result = format_thread_for_prompt(messages)

        assert "## Previous Rejection Feedback" in result
        assert "Rejection #1" in result
        assert "gatekeeper" in result
        assert "Tests fail: 3 failures" in result

    def test_multiple_rejections_numbered(self):
        from orchestrator.task_thread import format_thread_for_prompt

        messages = [
            {"role": "rejection", "content": "First problem", "author": "gk"},
            {"role": "rejection", "content": "Second problem", "author": "gk"},
        ]
        result = format_thread_for_prompt(messages)

        assert "Rejection #1" in result
        assert "Rejection #2" in result
        assert "First problem" in result
        assert "Second problem" in result

    def test_mixed_roles_only_rejections_shown(self):
        from orchestrator.task_thread import format_thread_for_prompt

        messages = [
            {"role": "info", "content": "info message"},
            {"role": "rejection", "content": "reject message", "author": "gk"},
        ]
        result = format_thread_for_prompt(messages)

        assert "Rejection #1" in result
        assert "reject message" in result
        assert "info message" not in result


class TestCleanupThread:
    """Tests for cleanup_thread."""

    def test_cleanup_deletes_thread_file(self, tmp_path):
        threads_dir = make_threads_dir(tmp_path)

        with patch("orchestrator.task_thread.get_threads_dir", return_value=threads_dir):
            from orchestrator.task_thread import post_message, cleanup_thread, get_thread

            post_message("abc123", role="rejection", content="Feedback")
            assert len(get_thread("abc123")) == 1

            result = cleanup_thread("abc123")
            assert result is True
            assert get_thread("abc123") == []

    def test_cleanup_nonexistent_returns_false(self, tmp_path):
        threads_dir = make_threads_dir(tmp_path)

        with patch("orchestrator.task_thread.get_threads_dir", return_value=threads_dir):
            from orchestrator.task_thread import cleanup_thread

            result = cleanup_thread("nonexistent")
            assert result is False


class TestRejectWithFeedbackPostsMessage:
    """Test that reject_with_feedback posts a thread message."""

    def test_reject_with_feedback_posts_thread_message(self, tmp_path):
        threads_dir = make_threads_dir(tmp_path)

        task = {
            "id": "task123",
            "pr_number": None,
        }
        result_data = {
            "comment": "Tests failed: 2 errors in foo.py",
        }
        task_dir = tmp_path / "task123"
        task_dir.mkdir()

        mock_tasks = type("Tasks", (), {
            "reject": lambda self, task_id, reason, rejected_by=None: {"id": task_id},
        })()
        mock_sdk = type("SDK", (), {"tasks": mock_tasks})()

        with (
            patch("orchestrator.task_thread.get_threads_dir", return_value=threads_dir),
            patch("orchestrator.queue_utils.get_sdk", return_value=mock_sdk),
            patch("orchestrator.config.get_base_branch", return_value="main"),
        ):
            from orchestrator import queue_utils
            queue_utils._sdk = mock_sdk  # inject directly to bypass lazy init

            from orchestrator.steps import reject_with_feedback
            reject_with_feedback(task, result_data, task_dir)

        with patch("orchestrator.task_thread.get_threads_dir", return_value=threads_dir):
            from orchestrator.task_thread import get_thread

            messages = get_thread("task123")
            assert len(messages) >= 1
            rejection_msgs = [m for m in messages if m["role"] == "rejection"]
            assert len(rejection_msgs) == 1
            assert "Tests failed" in rejection_msgs[0]["content"]
            assert rejection_msgs[0]["author"] == "gatekeeper"
