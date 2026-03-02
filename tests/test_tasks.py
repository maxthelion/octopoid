"""Tests for octopoid.tasks — task lifecycle CRUD and query operations.

Covers the major uncovered paths:
- _transition helper
- unclaim_task, complete_task, accept_completion, reject_completion
- review_reject_task (normal and escalation paths)
- get_review_feedback (various content scenarios)
- reset_task, hold_task
- mark_needs_continuation, resume_task
- find_task_by_id, get_continuation_tasks
- is_task_still_valid, get_task_by_id error path
- list_tasks (scope filter, exception path)
- create_task (project branch lookup)
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# _transition helper
# =============================================================================


class TestTransition:
    """Tests for the _transition internal helper."""

    def test_transition_calls_sdk_update(self, mock_sdk_for_unit_tests):
        """_transition calls sdk.tasks.update with the specified queue."""
        from octopoid.tasks import _transition

        mock_sdk_for_unit_tests.tasks.update.return_value = {"id": "abc", "queue": "incoming"}
        result = _transition("abc", "incoming")

        mock_sdk_for_unit_tests.tasks.update.assert_called_once_with("abc", queue="incoming")
        assert result["queue"] == "incoming"

    def test_transition_passes_extra_kwargs(self, mock_sdk_for_unit_tests):
        """_transition passes **sdk_kwargs to sdk.tasks.update."""
        from octopoid.tasks import _transition

        mock_sdk_for_unit_tests.tasks.update.return_value = {"id": "abc"}
        _transition("abc", "failed", claimed_by=None)

        mock_sdk_for_unit_tests.tasks.update.assert_called_once_with(
            "abc", queue="failed", claimed_by=None
        )

    def test_transition_calls_log_fn_with_logger(self, mock_sdk_for_unit_tests):
        """_transition calls log_fn with the task logger when provided."""
        from octopoid.tasks import _transition

        mock_sdk_for_unit_tests.tasks.update.return_value = {"id": "abc"}
        mock_logger = MagicMock()
        log_fn = MagicMock()

        with patch("octopoid.tasks.get_task_logger", return_value=mock_logger):
            _transition("abc", "done", log_fn=log_fn)

        log_fn.assert_called_once_with(mock_logger)

    def test_transition_calls_cleanup_when_requested(self, mock_sdk_for_unit_tests):
        """_transition calls cleanup_task_worktree when cleanup_worktree=True."""
        from octopoid.tasks import _transition

        mock_sdk_for_unit_tests.tasks.update.return_value = {"id": "abc"}

        with patch("octopoid.git_utils.cleanup_task_worktree") as mock_cleanup:
            _transition("abc", "done", cleanup_worktree=True, push_commits=True)

        mock_cleanup.assert_called_once_with("abc", push_commits=True)


# =============================================================================
# unclaim_task
# =============================================================================


class TestUnclaimTask:
    """Tests for unclaim_task."""

    def test_unclaim_task_moves_to_incoming(self, mock_sdk_for_unit_tests):
        """unclaim_task transitions the task back to incoming queue."""
        from octopoid.tasks import unclaim_task

        mock_sdk_for_unit_tests.tasks.update.return_value = {"id": "abc", "queue": "incoming"}
        result = unclaim_task("abc")

        mock_sdk_for_unit_tests.tasks.update.assert_called_once_with(
            "abc", queue="incoming", claimed_by=None
        )


# =============================================================================
# complete_task
# =============================================================================


class TestCompleteTask:
    """Tests for complete_task."""

    def test_complete_task_calls_accept(self, mock_sdk_for_unit_tests):
        """complete_task calls sdk.tasks.accept."""
        from octopoid.tasks import complete_task

        mock_sdk_for_unit_tests.tasks.accept.return_value = {"id": "abc", "queue": "done"}

        with patch("octopoid.task_notes.cleanup_task_notes"):
            result = complete_task("abc")

        mock_sdk_for_unit_tests.tasks.accept.assert_called_once_with(
            "abc", accepted_by="complete_task"
        )

    def test_complete_task_cleans_up_notes(self, mock_sdk_for_unit_tests):
        """complete_task calls cleanup_task_notes."""
        from octopoid.tasks import complete_task

        with patch("octopoid.task_notes.cleanup_task_notes") as mock_cleanup:
            complete_task("abc")

        mock_cleanup.assert_called_once_with("abc")


# =============================================================================
# accept_completion
# =============================================================================


class TestAcceptCompletion:
    """Tests for accept_completion."""

    def test_accept_completion_calls_sdk_accept(self, mock_sdk_for_unit_tests):
        """accept_completion calls sdk.tasks.accept with accepted_by."""
        from octopoid.tasks import accept_completion

        mock_sdk_for_unit_tests.tasks.accept.return_value = {"id": "abc", "queue": "done"}
        mock_logger = MagicMock()

        with patch("octopoid.tasks.get_task_logger", return_value=mock_logger), \
             patch("octopoid.git_utils.cleanup_task_worktree"), \
             patch("octopoid.task_notes.cleanup_task_notes"), \
             patch("octopoid.task_thread.cleanup_thread"):
            result = accept_completion("abc", accepted_by="reviewer")

        mock_sdk_for_unit_tests.tasks.accept.assert_called_once_with(
            "abc", accepted_by="reviewer"
        )

    def test_accept_completion_defaults_accepted_by(self, mock_sdk_for_unit_tests):
        """accept_completion defaults accepted_by to 'unknown' when None."""
        from octopoid.tasks import accept_completion

        mock_logger = MagicMock()

        with patch("octopoid.tasks.get_task_logger", return_value=mock_logger), \
             patch("octopoid.git_utils.cleanup_task_worktree"), \
             patch("octopoid.task_notes.cleanup_task_notes"), \
             patch("octopoid.task_thread.cleanup_thread"):
            accept_completion("abc")

        mock_sdk_for_unit_tests.tasks.accept.assert_called_once_with(
            "abc", accepted_by="unknown"
        )

    def test_accept_completion_cleans_up_worktree_and_notes(self, mock_sdk_for_unit_tests):
        """accept_completion calls cleanup_task_worktree, cleanup_task_notes, cleanup_thread."""
        from octopoid.tasks import accept_completion

        mock_logger = MagicMock()

        with patch("octopoid.tasks.get_task_logger", return_value=mock_logger), \
             patch("octopoid.git_utils.cleanup_task_worktree") as mock_wt, \
             patch("octopoid.task_notes.cleanup_task_notes") as mock_notes, \
             patch("octopoid.task_thread.cleanup_thread") as mock_thread:
            accept_completion("abc")

        mock_wt.assert_called_once_with("abc", push_commits=True)
        mock_notes.assert_called_once_with("abc")
        mock_thread.assert_called_once_with("abc")

    def test_accept_completion_logs_accepted(self, mock_sdk_for_unit_tests):
        """accept_completion logs the accepted event."""
        from octopoid.tasks import accept_completion

        mock_logger = MagicMock()

        with patch("octopoid.tasks.get_task_logger", return_value=mock_logger), \
             patch("octopoid.git_utils.cleanup_task_worktree"), \
             patch("octopoid.task_notes.cleanup_task_notes"), \
             patch("octopoid.task_thread.cleanup_thread"):
            accept_completion("abc", accepted_by="auto")

        mock_logger.log_accepted.assert_called_once_with(accepted_by="auto")


# =============================================================================
# reject_completion
# =============================================================================


class TestRejectCompletion:
    """Tests for reject_completion."""

    def test_reject_completion_calls_sdk_reject(self, mock_sdk_for_unit_tests):
        """reject_completion calls sdk.tasks.reject with reason and rejected_by."""
        from octopoid.tasks import reject_completion

        mock_sdk_for_unit_tests.tasks.reject.return_value = {"id": "abc", "queue": "incoming"}
        mock_logger = MagicMock()

        with patch("octopoid.tasks.get_task_logger", return_value=mock_logger), \
             patch("octopoid.git_utils.cleanup_task_worktree"):
            reject_completion("abc", reason="Not done yet", accepted_by="reviewer")

        mock_sdk_for_unit_tests.tasks.reject.assert_called_once_with(
            task_id="abc",
            reason="Not done yet",
            rejected_by="reviewer",
        )

    def test_reject_completion_cleans_up_worktree(self, mock_sdk_for_unit_tests):
        """reject_completion calls cleanup_task_worktree."""
        from octopoid.tasks import reject_completion

        mock_logger = MagicMock()

        with patch("octopoid.tasks.get_task_logger", return_value=mock_logger), \
             patch("octopoid.git_utils.cleanup_task_worktree") as mock_wt:
            reject_completion("abc", reason="reason")

        mock_wt.assert_called_once_with("abc", push_commits=True)

    def test_reject_completion_logs_rejected(self, mock_sdk_for_unit_tests):
        """reject_completion logs the rejection event."""
        from octopoid.tasks import reject_completion

        mock_logger = MagicMock()

        with patch("octopoid.tasks.get_task_logger", return_value=mock_logger), \
             patch("octopoid.git_utils.cleanup_task_worktree"):
            reject_completion("abc", reason="bad work", accepted_by="gatekeeper")

        mock_logger.log_rejected.assert_called_once_with(
            reason="bad work",
            rejected_by="gatekeeper",
        )


# =============================================================================
# review_reject_task
# =============================================================================


class TestReviewRejectTask:
    """Tests for review_reject_task.

    Note: review_reject_task has an internal scoping quirk — the
    `from .sdk import get_sdk` inside `if escalated:` marks `get_sdk` as
    a local variable throughout the entire function. SDK calls earlier in
    the function body raise UnboundLocalError (silently caught). The function
    still returns the correct tuple and calls post_message / cleanup_task_worktree.
    """

    def test_returns_task_id_and_rejected_action(self, mock_sdk_for_unit_tests):
        """review_reject_task returns (task_id, 'rejected') for normal rejections."""
        from octopoid.tasks import review_reject_task

        with patch("octopoid.task_thread.post_message"), \
             patch("octopoid.git_utils.cleanup_task_worktree"):
            task_id, action = review_reject_task("abc", feedback="Fix the tests")

        assert task_id == "abc"
        assert action == "rejected"

    def test_posts_feedback_as_thread_message(self, mock_sdk_for_unit_tests):
        """review_reject_task posts feedback as a rejection message on the task thread."""
        from octopoid.tasks import review_reject_task

        with patch("octopoid.task_thread.post_message") as mock_post, \
             patch("octopoid.git_utils.cleanup_task_worktree"):
            review_reject_task("abc", feedback="Needs tests", rejected_by="reviewer")

        mock_post.assert_called_once_with(
            "abc", role="rejection", content="Needs tests", author="reviewer"
        )

    def test_cleans_up_worktree(self, mock_sdk_for_unit_tests):
        """review_reject_task cleans up the task worktree."""
        from octopoid.tasks import review_reject_task

        with patch("octopoid.task_thread.post_message"), \
             patch("octopoid.git_utils.cleanup_task_worktree") as mock_cleanup:
            review_reject_task("abc", feedback="Not done")

        mock_cleanup.assert_called_once_with("abc", push_commits=True)

    def test_handles_post_message_exception_gracefully(self, mock_sdk_for_unit_tests):
        """review_reject_task continues if post_message raises."""
        from octopoid.tasks import review_reject_task

        with patch("octopoid.task_thread.post_message", side_effect=Exception("thread error")), \
             patch("octopoid.git_utils.cleanup_task_worktree"):
            # Should not raise
            task_id, action = review_reject_task("abc", feedback="Fix it")

        assert task_id == "abc"

    def test_uses_reviewer_as_default_author(self, mock_sdk_for_unit_tests):
        """review_reject_task uses 'reviewer' as default author when rejected_by is None."""
        from octopoid.tasks import review_reject_task

        with patch("octopoid.task_thread.post_message") as mock_post, \
             patch("octopoid.git_utils.cleanup_task_worktree"):
            review_reject_task("abc", feedback="Feedback text")

        # When rejected_by is None, author should default to "reviewer"
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["author"] == "reviewer"


# =============================================================================
# get_review_feedback
# =============================================================================


class TestGetReviewFeedback:
    """Tests for get_review_feedback."""

    def test_returns_none_when_task_not_found(self, mock_sdk_for_unit_tests):
        """get_review_feedback returns None when task doesn't exist."""
        from octopoid.tasks import get_review_feedback

        mock_sdk_for_unit_tests.tasks.get.return_value = None
        assert get_review_feedback("abc") is None

    def test_returns_none_when_no_content(self, mock_sdk_for_unit_tests):
        """get_review_feedback returns None when task has no content."""
        from octopoid.tasks import get_review_feedback

        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "content": ""}
        assert get_review_feedback("abc") is None

    def test_returns_rejection_section_content(self, mock_sdk_for_unit_tests):
        """get_review_feedback extracts ## Rejection Notice sections."""
        from octopoid.tasks import get_review_feedback

        content = (
            "# Task\n\n"
            "## Rejection Notice\n\n"
            "Please fix the tests.\n\n"
            "## Something Else\n\n"
            "More content.\n"
        )
        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "content": content}
        result = get_review_feedback("abc")

        assert result is not None
        assert "Please fix the tests." in result

    def test_returns_legacy_review_feedback_section(self, mock_sdk_for_unit_tests):
        """get_review_feedback falls back to legacy ## Review Feedback sections."""
        from octopoid.tasks import get_review_feedback

        content = (
            "# Task\n\n"
            "## Review Feedback (rejection #1)\n\n"
            "Old-style feedback.\n\n"
            "## End\n"
        )
        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "content": content}
        result = get_review_feedback("abc")

        assert result is not None
        assert "Old-style feedback." in result

    def test_returns_none_when_no_sections(self, mock_sdk_for_unit_tests):
        """get_review_feedback returns None when task content has no feedback sections."""
        from octopoid.tasks import get_review_feedback

        content = "# Task\n\n## Context\n\nDo some work.\n\n## Acceptance Criteria\n\n- [ ] Done\n"
        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "content": content}
        assert get_review_feedback("abc") is None

    def test_joins_multiple_rejection_sections(self, mock_sdk_for_unit_tests):
        """get_review_feedback joins multiple rejection sections with separator."""
        from octopoid.tasks import get_review_feedback

        content = (
            "# Task\n\n"
            "## Rejection Notice\n\n"
            "First rejection.\n\n"
            "## Context\n\nSomething\n\n"
            "## Rejection Notice\n\n"
            "Second rejection.\n\n"
        )
        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "content": content}
        result = get_review_feedback("abc")

        assert result is not None
        assert "First rejection." in result
        assert "Second rejection." in result
        assert "---" in result  # separator


# =============================================================================
# reset_task
# =============================================================================


class TestResetTask:
    """Tests for reset_task."""

    def test_reset_task_moves_to_incoming(self, mock_sdk_for_unit_tests):
        """reset_task updates task to incoming queue with clean state."""
        from octopoid.tasks import reset_task

        mock_sdk_for_unit_tests.tasks.get.return_value = {
            "id": "abc", "queue": "failed"
        }
        result = reset_task("abc")

        assert result["new_queue"] == "incoming"
        assert result["old_queue"] == "failed"
        assert result["action"] == "reset"

    def test_reset_task_calls_sdk_update(self, mock_sdk_for_unit_tests):
        """reset_task calls sdk.tasks.update to reset state."""
        from octopoid.tasks import reset_task

        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "queue": "claimed"}
        reset_task("abc")

        mock_sdk_for_unit_tests.tasks.update.assert_called_once_with(
            "abc",
            queue="incoming",
            claimed_by=None,
            claimed_at=None,
            checks=None,
            check_results=None,
            rejection_count=0,
        )

    def test_reset_task_raises_lookup_when_not_found(self, mock_sdk_for_unit_tests):
        """reset_task raises RuntimeError (wrapping LookupError) when task not found."""
        from octopoid.tasks import reset_task

        mock_sdk_for_unit_tests.tasks.get.return_value = None

        with pytest.raises(RuntimeError, match="reset task abc"):
            reset_task("abc")

    def test_reset_task_raises_runtime_error_on_exception(self, mock_sdk_for_unit_tests):
        """reset_task raises RuntimeError when SDK update fails."""
        from octopoid.tasks import reset_task

        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "queue": "claimed"}
        mock_sdk_for_unit_tests.tasks.update.side_effect = Exception("network error")

        with pytest.raises(RuntimeError, match="Failed to reset task"):
            reset_task("abc")


# =============================================================================
# hold_task
# =============================================================================


class TestHoldTask:
    """Tests for hold_task."""

    def test_hold_task_moves_to_escalated(self, mock_sdk_for_unit_tests):
        """hold_task updates task to escalated queue."""
        from octopoid.tasks import hold_task

        mock_sdk_for_unit_tests.tasks.get.return_value = {
            "id": "abc", "queue": "claimed"
        }
        result = hold_task("abc")

        assert result["new_queue"] == "escalated"
        assert result["old_queue"] == "claimed"
        assert result["action"] == "held"

    def test_hold_task_calls_sdk_update(self, mock_sdk_for_unit_tests):
        """hold_task calls sdk.tasks.update to move to escalated."""
        from octopoid.tasks import hold_task

        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "queue": "incoming"}
        hold_task("abc")

        mock_sdk_for_unit_tests.tasks.update.assert_called_once_with(
            "abc",
            queue="escalated",
            claimed_by=None,
            claimed_at=None,
            checks=None,
            check_results=None,
        )

    def test_hold_task_raises_when_not_found(self, mock_sdk_for_unit_tests):
        """hold_task raises RuntimeError (wrapping LookupError) when task not found."""
        from octopoid.tasks import hold_task

        mock_sdk_for_unit_tests.tasks.get.return_value = None

        with pytest.raises(RuntimeError, match="hold task abc"):
            hold_task("abc")

    def test_hold_task_raises_runtime_error_on_exception(self, mock_sdk_for_unit_tests):
        """hold_task raises RuntimeError when SDK update fails."""
        from octopoid.tasks import hold_task

        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "queue": "claimed"}
        mock_sdk_for_unit_tests.tasks.update.side_effect = Exception("network error")

        with pytest.raises(RuntimeError, match="Failed to hold task"):
            hold_task("abc")


# =============================================================================
# mark_needs_continuation
# =============================================================================


class TestMarkNeedsContinuation:
    """Tests for mark_needs_continuation."""

    def test_mark_needs_continuation_calls_sdk_update(self, mock_sdk_for_unit_tests):
        """mark_needs_continuation calls sdk.tasks.update with needs_continuation queue."""
        from octopoid.tasks import mark_needs_continuation

        mark_needs_continuation("abc", reason="Hit turn limit", branch_name="agent/abc", agent_name="impl")

        mock_sdk_for_unit_tests.tasks.update.assert_called_once_with(
            "abc",
            queue="needs_continuation",
            reason="Hit turn limit",
            branch_name="agent/abc",
            agent_name="impl",
        )


# =============================================================================
# resume_task
# =============================================================================


class TestResumeTask:
    """Tests for resume_task."""

    def test_resume_task_calls_sdk_update(self, mock_sdk_for_unit_tests):
        """resume_task calls sdk.tasks.update to move to claimed queue."""
        from octopoid.tasks import resume_task

        with patch("octopoid.tasks.get_orchestrator_id", return_value="orch-1"):
            resume_task("abc", agent_name="impl")

        mock_sdk_for_unit_tests.tasks.update.assert_called_once_with(
            "abc",
            queue="claimed",
            claimed_by="impl",
            orchestrator_id="orch-1",
        )

    def test_resume_task_defaults_agent_name(self, mock_sdk_for_unit_tests):
        """resume_task defaults claimed_by to 'unknown' when agent_name is None."""
        from octopoid.tasks import resume_task

        with patch("octopoid.tasks.get_orchestrator_id", return_value="orch-1"):
            resume_task("abc")

        mock_sdk_for_unit_tests.tasks.update.assert_called_once_with(
            "abc",
            queue="claimed",
            claimed_by="unknown",
            orchestrator_id="orch-1",
        )


# =============================================================================
# find_task_by_id
# =============================================================================


class TestFindTaskById:
    """Tests for find_task_by_id."""

    def test_returns_task_when_found(self, mock_sdk_for_unit_tests):
        """find_task_by_id returns task dict when task exists."""
        from octopoid.tasks import find_task_by_id

        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "queue": "incoming"}
        result = find_task_by_id("abc")

        assert result == {"id": "abc", "queue": "incoming"}

    def test_returns_none_when_not_found(self, mock_sdk_for_unit_tests):
        """find_task_by_id returns None when task doesn't exist."""
        from octopoid.tasks import find_task_by_id

        mock_sdk_for_unit_tests.tasks.get.return_value = None
        assert find_task_by_id("abc") is None

    def test_filters_by_queue(self, mock_sdk_for_unit_tests):
        """find_task_by_id returns None when task queue doesn't match filter."""
        from octopoid.tasks import find_task_by_id

        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "queue": "done"}
        result = find_task_by_id("abc", queues=["incoming", "claimed"])

        assert result is None

    def test_returns_task_when_queue_matches(self, mock_sdk_for_unit_tests):
        """find_task_by_id returns task when queue is in the allowed list."""
        from octopoid.tasks import find_task_by_id

        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "queue": "incoming"}
        result = find_task_by_id("abc", queues=["incoming", "claimed"])

        assert result is not None
        assert result["id"] == "abc"


# =============================================================================
# get_continuation_tasks
# =============================================================================


class TestGetContinuationTasks:
    """Tests for get_continuation_tasks."""

    def test_returns_all_tasks_without_filter(self, mock_sdk_for_unit_tests):
        """get_continuation_tasks returns all needs_continuation tasks when no agent filter."""
        from octopoid.tasks import get_continuation_tasks

        mock_sdk_for_unit_tests.tasks.list.return_value = [
            {"id": "t1", "content": "LAST_AGENT: agent-a", "scope": None},
            {"id": "t2", "content": "LAST_AGENT: agent-b", "scope": None},
        ]

        with patch("octopoid.tasks.get_scope", return_value=None):
            result = get_continuation_tasks()

        assert len(result) == 2

    def test_filters_by_agent_name(self, mock_sdk_for_unit_tests):
        """get_continuation_tasks filters by agent name when specified."""
        from octopoid.tasks import get_continuation_tasks

        mock_sdk_for_unit_tests.tasks.list.return_value = [
            {"id": "t1", "content": "LAST_AGENT: agent-a", "scope": None},
            {"id": "t2", "content": "LAST_AGENT: agent-b", "scope": None},
        ]

        with patch("octopoid.tasks.get_scope", return_value=None):
            result = get_continuation_tasks(agent_name="agent-a")

        assert len(result) == 1
        assert result[0]["id"] == "t1"

    def test_returns_empty_when_no_match(self, mock_sdk_for_unit_tests):
        """get_continuation_tasks returns empty list when no tasks match agent filter."""
        from octopoid.tasks import get_continuation_tasks

        mock_sdk_for_unit_tests.tasks.list.return_value = [
            {"id": "t1", "content": "LAST_AGENT: agent-x", "scope": None},
        ]

        with patch("octopoid.tasks.get_scope", return_value=None):
            result = get_continuation_tasks(agent_name="agent-z")

        assert result == []


# =============================================================================
# is_task_still_valid
# =============================================================================


class TestIsTaskStillValid:
    """Tests for is_task_still_valid."""

    def test_returns_true_when_task_in_active_queue(self, mock_sdk_for_unit_tests):
        """is_task_still_valid returns True when task is in an active queue (claimed)."""
        from octopoid.tasks import is_task_still_valid

        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "queue": "claimed"}
        assert is_task_still_valid("abc") is True

    def test_returns_false_when_task_not_found(self, mock_sdk_for_unit_tests):
        """is_task_still_valid returns False when task doesn't exist."""
        from octopoid.tasks import is_task_still_valid

        mock_sdk_for_unit_tests.tasks.get.return_value = None
        assert is_task_still_valid("abc") is False

    def test_returns_false_when_task_in_done_queue(self, mock_sdk_for_unit_tests):
        """is_task_still_valid returns False when task is in done (not an active queue)."""
        from octopoid.tasks import is_task_still_valid

        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "queue": "done"}
        assert is_task_still_valid("abc") is False


# =============================================================================
# get_task_by_id
# =============================================================================


class TestGetTaskById:
    """Tests for get_task_by_id."""

    def test_returns_task_on_success(self, mock_sdk_for_unit_tests):
        """get_task_by_id returns task dict from SDK."""
        from octopoid.tasks import get_task_by_id

        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc", "queue": "incoming"}
        result = get_task_by_id("abc")

        assert result == {"id": "abc", "queue": "incoming"}

    def test_returns_none_on_exception(self, mock_sdk_for_unit_tests):
        """get_task_by_id returns None when SDK raises an exception."""
        from octopoid.tasks import get_task_by_id

        mock_sdk_for_unit_tests.tasks.get.side_effect = Exception("connection refused")
        result = get_task_by_id("abc")

        assert result is None


# =============================================================================
# list_tasks
# =============================================================================


class TestListTasks:
    """Tests for list_tasks."""

    def test_filters_by_scope(self, mock_sdk_for_unit_tests):
        """list_tasks filters tasks by scope when scope is configured."""
        from octopoid.tasks import list_tasks

        mock_sdk_for_unit_tests.tasks.list.return_value = [
            {"id": "t1", "queue": "incoming", "scope": "project-a", "priority": "P1"},
            {"id": "t2", "queue": "incoming", "scope": "project-b", "priority": "P1"},
        ]

        with patch("octopoid.tasks.get_scope", return_value="project-a"):
            result = list_tasks("incoming")

        assert len(result) == 1
        assert result[0]["id"] == "t1"

    def test_returns_empty_list_on_exception(self, mock_sdk_for_unit_tests):
        """list_tasks returns empty list when SDK raises an exception."""
        from octopoid.tasks import list_tasks

        mock_sdk_for_unit_tests.tasks.list.side_effect = Exception("server error")
        result = list_tasks("incoming")

        assert result == []

    def test_no_scope_filter_returns_all(self, mock_sdk_for_unit_tests):
        """list_tasks returns all tasks when no scope is configured."""
        from octopoid.tasks import list_tasks

        mock_sdk_for_unit_tests.tasks.list.return_value = [
            {"id": "t1", "scope": "project-a", "priority": "P1"},
            {"id": "t2", "scope": "project-b", "priority": "P1"},
        ]

        with patch("octopoid.tasks.get_scope", return_value=None):
            result = list_tasks("incoming")

        assert len(result) == 2


# =============================================================================
# create_task (project branch path)
# =============================================================================


class TestCreateTask:
    """Tests for create_task — project branch lookup paths."""

    def test_uses_project_branch_when_project_has_branch(self, mock_sdk_for_unit_tests):
        """create_task uses the project's branch when project_id is provided and project has a branch."""
        from octopoid.tasks import create_task

        mock_sdk_for_unit_tests.projects = MagicMock()
        mock_sdk_for_unit_tests.projects.get.return_value = {"branch": "feature/my-project"}

        mock_logger = MagicMock()
        with patch("octopoid.tasks.get_task_logger", return_value=mock_logger), \
             patch("octopoid.tasks.get_base_branch", return_value="main"):
            task_id = create_task(
                title="Test task",
                role="implement",
                context="Do something",
                acceptance_criteria=["- [ ] Done"],
                project_id="proj-1",
            )

        # The SDK create call should use the project's branch
        create_call = mock_sdk_for_unit_tests.tasks.create.call_args
        assert create_call is not None
        assert create_call[1]["branch"] == "feature/my-project"

    def test_warns_when_project_has_no_branch(self, mock_sdk_for_unit_tests, capsys):
        """create_task warns to stderr when project has no branch set."""
        from octopoid.tasks import create_task

        mock_sdk_for_unit_tests.projects = MagicMock()
        mock_sdk_for_unit_tests.projects.get.return_value = {"branch": None}

        mock_logger = MagicMock()
        with patch("octopoid.tasks.get_task_logger", return_value=mock_logger), \
             patch("octopoid.tasks.get_base_branch", return_value="main"):
            create_task(
                title="Test task",
                role="implement",
                context="Do something",
                acceptance_criteria=["- [ ] Done"],
                project_id="proj-no-branch",
            )

        captured = capsys.readouterr()
        assert "no branch" in captured.err.lower() or "WARNING" in captured.err

    def test_falls_back_to_base_branch_when_project_fetch_fails(
        self, mock_sdk_for_unit_tests
    ):
        """create_task falls back to base branch when project fetch raises."""
        from octopoid.tasks import create_task

        mock_sdk_for_unit_tests.projects = MagicMock()
        mock_sdk_for_unit_tests.projects.get.side_effect = Exception("not found")

        mock_logger = MagicMock()
        with patch("octopoid.tasks.get_task_logger", return_value=mock_logger), \
             patch("octopoid.tasks.get_base_branch", return_value="main"):
            task_id = create_task(
                title="Test task",
                role="implement",
                context="Do something",
                acceptance_criteria=["- [ ] Done"],
                project_id="proj-broken",
            )

        create_call = mock_sdk_for_unit_tests.tasks.create.call_args
        assert create_call[1]["branch"] == "main"


# =============================================================================
# cancel_task edge paths
# =============================================================================


class TestCancelTaskEdgePaths:
    """Tests for edge paths in cancel_task."""

    def test_server_404_treated_as_success(self, mock_sdk_for_unit_tests, tmp_path):
        """cancel_task treats a 404 from the server as successful deletion."""
        from octopoid.tasks import cancel_task

        mock_sdk_for_unit_tests.tasks.delete.side_effect = Exception("404: not found")

        with patch("octopoid.config.get_agents_runtime_dir", return_value=tmp_path / "agents"), \
             patch("octopoid.config.get_tasks_dir", return_value=tmp_path / "tasks"):
            result = cancel_task("abc")

        assert result["server_deleted"] is True
        assert result["errors"] == []

    def test_cancel_task_returns_correct_structure(self, mock_sdk_for_unit_tests, tmp_path):
        """cancel_task returns expected result dict keys."""
        from octopoid.tasks import cancel_task

        with patch("octopoid.config.get_agents_runtime_dir", return_value=tmp_path / "agents"), \
             patch("octopoid.config.get_tasks_dir", return_value=tmp_path / "tasks"):
            result = cancel_task("abc")

        assert "task_id" in result
        assert "killed_pid" in result
        assert "worktree_removed" in result
        assert "runtime_removed" in result
        assert "server_deleted" in result
        assert "errors" in result
        assert result["task_id"] == "abc"

# =============================================================================
# TaskSpec dataclass
# =============================================================================


class TestTaskSpec:
    """Tests for the TaskSpec dataclass."""

    def test_taskspec_required_fields(self):
        """TaskSpec requires title, role, context, and acceptance_criteria."""
        from octopoid.tasks import TaskSpec

        spec = TaskSpec(
            title="Test",
            role="implement",
            context="Do something",
            acceptance_criteria=["- [ ] Done"],
        )
        assert spec.title == "Test"
        assert spec.role == "implement"
        assert spec.priority == "P1"
        assert spec.queue == "incoming"
        assert spec.created_by == "human"
        assert spec.blocked_by is None
        assert spec.project_id is None
        assert spec.breakdown_depth == 0

    def test_taskspec_optional_fields(self):
        """TaskSpec accepts all optional fields."""
        from octopoid.tasks import TaskSpec

        spec = TaskSpec(
            title="Test",
            role="implement",
            context="Context",
            acceptance_criteria=["- [ ] Done"],
            priority="P2",
            branch="feature/x",
            flow="custom",
            created_by="agent",
            blocked_by="abc123",
            project_id="proj-1",
            queue="provisional",
            checks=["ci"],
            breakdown_depth=2,
        )
        assert spec.priority == "P2"
        assert spec.branch == "feature/x"
        assert spec.blocked_by == "abc123"
        assert spec.checks == ["ci"]
        assert spec.breakdown_depth == 2


# =============================================================================
# _resolve_branch helper
# =============================================================================


class TestResolveBranch:
    """Tests for the _resolve_branch helper."""

    def test_uses_spec_branch_when_set(self):
        """_resolve_branch returns spec.branch directly when set."""
        from octopoid.tasks import TaskSpec, _resolve_branch

        spec = TaskSpec(
            title="T", role="r", context="c",
            acceptance_criteria=[], branch="feature/my-branch"
        )
        result = _resolve_branch(spec)
        assert result == "feature/my-branch"

    def test_fetches_project_branch_when_no_branch(self, mock_sdk_for_unit_tests):
        """_resolve_branch fetches project branch when spec.branch is None and project_id is set."""
        from octopoid.tasks import TaskSpec, _resolve_branch

        mock_sdk_for_unit_tests.projects = MagicMock()
        mock_sdk_for_unit_tests.projects.get.return_value = {"branch": "feature/project"}

        spec = TaskSpec(
            title="T", role="r", context="c",
            acceptance_criteria=[], project_id="proj-1"
        )
        result = _resolve_branch(spec)
        assert result == "feature/project"

    def test_falls_back_to_base_when_project_has_no_branch(
        self, mock_sdk_for_unit_tests, capsys
    ):
        """_resolve_branch falls back to base branch and warns when project has no branch."""
        from octopoid.tasks import TaskSpec, _resolve_branch

        mock_sdk_for_unit_tests.projects = MagicMock()
        mock_sdk_for_unit_tests.projects.get.return_value = {"branch": None}

        spec = TaskSpec(
            title="T", role="r", context="c",
            acceptance_criteria=[], project_id="proj-no-branch"
        )
        with patch("octopoid.tasks.get_base_branch", return_value="main"):
            result = _resolve_branch(spec)

        assert result == "main"
        captured = capsys.readouterr()
        assert "no branch" in captured.err.lower() or "WARNING" in captured.err

    def test_falls_back_to_base_when_project_fetch_raises(self, mock_sdk_for_unit_tests):
        """_resolve_branch falls back to base branch when project fetch raises."""
        from octopoid.tasks import TaskSpec, _resolve_branch

        mock_sdk_for_unit_tests.projects = MagicMock()
        mock_sdk_for_unit_tests.projects.get.side_effect = Exception("network error")

        spec = TaskSpec(
            title="T", role="r", context="c",
            acceptance_criteria=[], project_id="proj-broken"
        )
        with patch("octopoid.tasks.get_base_branch", return_value="main"):
            result = _resolve_branch(spec)

        assert result == "main"

    def test_falls_back_to_base_when_no_project(self):
        """_resolve_branch falls back to get_base_branch when no branch and no project_id."""
        from octopoid.tasks import TaskSpec, _resolve_branch

        spec = TaskSpec(title="T", role="r", context="c", acceptance_criteria=[])
        with patch("octopoid.tasks.get_base_branch", return_value="main"):
            result = _resolve_branch(spec)

        assert result == "main"


# =============================================================================
# _normalize_criteria helper
# =============================================================================


class TestNormalizeCriteria:
    """Tests for the _normalize_criteria helper."""

    def test_list_with_checkbox_lines_unchanged(self):
        """_normalize_criteria preserves lines that already start with '- [ ]' or '- [x]'."""
        from octopoid.tasks import _normalize_criteria

        criteria = ["- [ ] First", "- [x] Already done"]
        result = _normalize_criteria(criteria)
        assert result == ["- [ ] First", "- [x] Already done"]

    def test_plain_strings_get_checkbox_prefix(self):
        """_normalize_criteria adds '- [ ] ' prefix to plain strings."""
        from octopoid.tasks import _normalize_criteria

        result = _normalize_criteria(["Do something", "And another thing"])
        assert result == ["- [ ] Do something", "- [ ] And another thing"]

    def test_string_input_split_by_lines(self):
        """_normalize_criteria splits a string input into lines."""
        from octopoid.tasks import _normalize_criteria

        result = _normalize_criteria("First item\nSecond item\n")
        assert result == ["- [ ] First item", "- [ ] Second item"]

    def test_string_input_empty_lines_skipped(self):
        """_normalize_criteria skips blank lines when splitting a string."""
        from octopoid.tasks import _normalize_criteria

        result = _normalize_criteria("First\n\nSecond\n   \nThird")
        assert result == ["- [ ] First", "- [ ] Second", "- [ ] Third"]

    def test_mixed_checkbox_and_plain(self):
        """_normalize_criteria handles a mix of already-prefixed and plain lines."""
        from octopoid.tasks import _normalize_criteria

        result = _normalize_criteria(["- [ ] Already set", "Plain line"])
        assert result == ["- [ ] Already set", "- [ ] Plain line"]

    def test_empty_list(self):
        """_normalize_criteria returns empty list for empty input."""
        from octopoid.tasks import _normalize_criteria

        assert _normalize_criteria([]) == []


# =============================================================================
# _build_task_content helper
# =============================================================================


class TestBuildTaskContent:
    """Tests for the _build_task_content helper."""

    def test_contains_task_id_and_title(self):
        """_build_task_content embeds task_id and title in the header."""
        from octopoid.tasks import TaskSpec, _build_task_content

        spec = TaskSpec(title="My Task", role="implement", context="ctx", acceptance_criteria=[])
        content = _build_task_content(spec, "abc123", "main", ["- [ ] Done"])
        assert "# [TASK-abc123] My Task" in content

    def test_contains_role_priority_branch(self):
        """_build_task_content includes ROLE, PRIORITY, and BRANCH metadata."""
        from octopoid.tasks import TaskSpec, _build_task_content

        spec = TaskSpec(
            title="T", role="review", context="ctx",
            acceptance_criteria=[], priority="P0"
        )
        content = _build_task_content(spec, "id1", "feature/x", [])
        assert "ROLE: review" in content
        assert "PRIORITY: P0" in content
        assert "BRANCH: feature/x" in content

    def test_blocked_by_line_present_when_set(self):
        """_build_task_content includes BLOCKED_BY when spec.blocked_by is set."""
        from octopoid.tasks import TaskSpec, _build_task_content

        spec = TaskSpec(
            title="T", role="r", context="ctx",
            acceptance_criteria=[], blocked_by="dep123"
        )
        content = _build_task_content(spec, "id1", "main", [])
        assert "BLOCKED_BY: dep123" in content

    def test_blocked_by_line_absent_when_not_set(self):
        """_build_task_content omits BLOCKED_BY when spec.blocked_by is None."""
        from octopoid.tasks import TaskSpec, _build_task_content

        spec = TaskSpec(title="T", role="r", context="ctx", acceptance_criteria=[])
        content = _build_task_content(spec, "id1", "main", [])
        assert "BLOCKED_BY" not in content

    def test_checks_line_present_when_set(self):
        """_build_task_content includes CHECKS when spec.checks is set."""
        from octopoid.tasks import TaskSpec, _build_task_content

        spec = TaskSpec(
            title="T", role="r", context="ctx",
            acceptance_criteria=[], checks=["ci", "lint"]
        )
        content = _build_task_content(spec, "id1", "main", [])
        assert "CHECKS: ci,lint" in content

    def test_breakdown_depth_line_present_when_positive(self):
        """_build_task_content includes BREAKDOWN_DEPTH when > 0."""
        from octopoid.tasks import TaskSpec, _build_task_content

        spec = TaskSpec(
            title="T", role="r", context="ctx",
            acceptance_criteria=[], breakdown_depth=2
        )
        content = _build_task_content(spec, "id1", "main", [])
        assert "BREAKDOWN_DEPTH: 2" in content

    def test_breakdown_depth_line_absent_when_zero(self):
        """_build_task_content omits BREAKDOWN_DEPTH when == 0."""
        from octopoid.tasks import TaskSpec, _build_task_content

        spec = TaskSpec(title="T", role="r", context="ctx", acceptance_criteria=[])
        content = _build_task_content(spec, "id1", "main", [])
        assert "BREAKDOWN_DEPTH" not in content

    def test_criteria_included_in_content(self):
        """_build_task_content includes acceptance criteria lines."""
        from octopoid.tasks import TaskSpec, _build_task_content

        spec = TaskSpec(title="T", role="r", context="ctx", acceptance_criteria=[])
        content = _build_task_content(spec, "id1", "main", ["- [ ] Step A", "- [ ] Step B"])
        assert "- [ ] Step A" in content
        assert "- [ ] Step B" in content
