"""Tests for agent_result message posting after stdout inference.

Verifies that after infer_result_from_stdout() returns a classification,
the scheduler posts an agent_result message on the task thread via
sdk.messages.create().

Tests cover:
- handle_agent_result() posts message for implementer agents
- handle_agent_result_via_flow() posts message for gatekeeper agents
- handle_fixer_result() posts message for fixer agents
- Message type is "agent_result"
- Message content includes agent_name, role, and classification fields
- Message posting failure does not interrupt result handling
"""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_task_dir(tmp_path):
    """Create a minimal task directory structure."""
    task_dir = tmp_path / "test123"
    task_dir.mkdir()
    (task_dir / "worktree").mkdir()
    return task_dir


@pytest.fixture
def mock_sdk():
    """SDK mock with tasks and messages."""
    sdk = MagicMock()
    sdk.tasks = MagicMock()
    sdk.messages = MagicMock()
    return sdk


@pytest.fixture
def sample_task():
    return {
        "id": "test123",
        "title": "Test task",
        "role": "implement",
        "queue": "claimed",
        "flow": "default",
    }


# ---------------------------------------------------------------------------
# Test: handle_agent_result posts agent_result message
# ---------------------------------------------------------------------------

class TestHandleAgentResultPostsMessage:
    """handle_agent_result() posts an agent_result message after inference."""

    def test_done_outcome_posts_agent_result_message(self, tmp_task_dir, mock_sdk, sample_task):
        """When outcome is 'done', an agent_result message is posted."""
        mock_sdk.tasks.get.return_value = sample_task

        inferred = {"outcome": "done"}

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value=inferred), \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = []
            mock_transition.to_state = "provisional"
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "implementer-1", tmp_task_dir)

        # Verify agent_result message was posted
        mock_sdk.messages.create.assert_called_once()
        call_kwargs = mock_sdk.messages.create.call_args[1]
        assert call_kwargs["task_id"] == "test123"
        assert call_kwargs["type"] == "agent_result"
        assert call_kwargs["from_actor"] == "agent"
        assert call_kwargs["to_actor"] == "human"
        content = call_kwargs["content"]
        assert "implementer-1" in content
        assert "implement" in content
        assert "done" in content

    def test_failed_outcome_posts_agent_result_message(self, tmp_task_dir, mock_sdk, sample_task):
        """When outcome is 'failed', an agent_result message is posted with reason."""
        mock_sdk.tasks.get.return_value = sample_task

        inferred = {"outcome": "failed", "reason": "Tests don't pass"}

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value=inferred):
            mock_qu.get_sdk.return_value = mock_sdk

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "implementer-1", tmp_task_dir)

        mock_sdk.messages.create.assert_called_once()
        call_kwargs = mock_sdk.messages.create.call_args[1]
        assert call_kwargs["type"] == "agent_result"
        content = call_kwargs["content"]
        assert "failed" in content
        assert "implementer-1" in content

    def test_message_includes_full_classification(self, tmp_task_dir, mock_sdk, sample_task):
        """Message content includes all classification fields."""
        mock_sdk.tasks.get.return_value = sample_task

        inferred = {"outcome": "failed", "reason": "Compilation error"}

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value=inferred):
            mock_qu.get_sdk.return_value = mock_sdk

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "implementer-1", tmp_task_dir)

        call_kwargs = mock_sdk.messages.create.call_args[1]
        content = call_kwargs["content"]
        assert "failed" in content
        assert "Compilation error" in content

    def test_message_posted_before_task_state_transition(self, tmp_task_dir, mock_sdk, sample_task):
        """Message is posted (sdk.messages.create called) before sdk.tasks.submit."""
        mock_sdk.tasks.get.return_value = sample_task
        call_order = []

        def record_messages_create(**kwargs):
            call_order.append("messages")
        def record_tasks_submit(**kwargs):
            call_order.append("tasks")

        mock_sdk.messages.create.side_effect = record_messages_create
        mock_sdk.tasks.submit.side_effect = record_tasks_submit

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "done"}), \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk
            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = []
            mock_transition.to_state = "provisional"
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "implementer-1", tmp_task_dir)

        # Message should be posted before the transition
        assert call_order.index("messages") < call_order.index("tasks")

    def test_message_posting_failure_does_not_block_result_handling(self, tmp_task_dir, mock_sdk, sample_task):
        """If sdk.messages.create raises, result handling still completes."""
        mock_sdk.tasks.get.return_value = sample_task
        mock_sdk.messages.create.side_effect = RuntimeError("messages API unavailable")

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "done"}), \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk
            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = []
            mock_transition.to_state = "provisional"
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from octopoid.scheduler import handle_agent_result
            # Should not raise despite messages.create failing
            result = handle_agent_result("test123", "implementer-1", tmp_task_dir)

        # Task still transitioned despite message failure
        assert result is True
        mock_sdk.tasks.submit.assert_called_once()


# ---------------------------------------------------------------------------
# Test: handle_agent_result_via_flow posts agent_result message
# ---------------------------------------------------------------------------

class TestHandleAgentResultViaFlowPostsMessage:
    """handle_agent_result_via_flow() posts an agent_result message after inference."""

    def test_approve_decision_posts_agent_result_message(self, tmp_task_dir, mock_sdk, sample_task):
        """Gatekeeper approve decision posts an agent_result message."""
        mock_sdk.tasks.get.return_value = sample_task

        inferred = {"status": "success", "decision": "approve", "comment": "LGTM"}

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value=inferred), \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk
            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.conditions = []
            mock_transition.runs = []
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from octopoid.scheduler import handle_agent_result_via_flow
            handle_agent_result_via_flow("test123", "gatekeeper-1", tmp_task_dir)

        mock_sdk.messages.create.assert_called_once()
        call_kwargs = mock_sdk.messages.create.call_args[1]
        assert call_kwargs["type"] == "agent_result"
        assert call_kwargs["task_id"] == "test123"
        content = call_kwargs["content"]
        assert "gatekeeper-1" in content
        assert "gatekeeper" in content
        assert "approve" in content

    def test_reject_decision_posts_agent_result_message(self, tmp_task_dir, mock_sdk, sample_task):
        """Gatekeeper reject decision posts an agent_result message."""
        mock_sdk.tasks.get.return_value = sample_task

        inferred = {"status": "success", "decision": "reject", "comment": "Needs more tests."}

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value=inferred), \
             patch("octopoid.steps.reject_with_feedback"), \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk
            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.conditions = []
            mock_transition.runs = []
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from octopoid.scheduler import handle_agent_result_via_flow
            handle_agent_result_via_flow("test123", "gatekeeper-1", tmp_task_dir)

        mock_sdk.messages.create.assert_called_once()
        call_kwargs = mock_sdk.messages.create.call_args[1]
        assert call_kwargs["type"] == "agent_result"
        content = call_kwargs["content"]
        assert "reject" in content

    def test_comment_field_excluded_from_json_summary(self, tmp_task_dir, mock_sdk, sample_task):
        """The 'comment' field (stdout tail) is excluded from the JSON summary in the message."""
        mock_sdk.tasks.get.return_value = sample_task

        long_comment = "A" * 2000
        inferred = {"status": "success", "decision": "approve", "comment": long_comment}

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value=inferred), \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk
            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.conditions = []
            mock_transition.runs = []
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from octopoid.scheduler import handle_agent_result_via_flow
            handle_agent_result_via_flow("test123", "gatekeeper-1", tmp_task_dir)

        call_kwargs = mock_sdk.messages.create.call_args[1]
        content = call_kwargs["content"]
        # The 2000-char comment should NOT appear in the JSON summary
        assert long_comment not in content
        # But decision should still be there
        assert "approve" in content


# ---------------------------------------------------------------------------
# Test: handle_fixer_result posts agent_result message
# ---------------------------------------------------------------------------

class TestHandleFixerResultPostsMessage:
    """handle_fixer_result() posts an agent_result message after inference."""

    def test_fixed_outcome_posts_agent_result_message(self, tmp_task_dir, mock_sdk):
        """Fixer 'fixed' outcome posts an agent_result message."""
        task = {"id": "test123", "queue": "requires-intervention", "flow": "default"}
        mock_sdk.tasks.get.return_value = task

        inferred = {"outcome": "fixed", "diagnosis": "Rebased cleanly", "fix_applied": "git rebase"}

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value=inferred), \
             patch("octopoid.result_handler._resume_flow"):

            mock_qu.get_sdk.return_value = mock_sdk

            from octopoid.scheduler import handle_fixer_result
            handle_fixer_result("test123", "fixer-1", tmp_task_dir)

        # agent_result message should be the first message.create call
        # (subsequent ones may come from the fixer's own messaging)
        assert mock_sdk.messages.create.called
        first_call_kwargs = mock_sdk.messages.create.call_args_list[0][1]
        assert first_call_kwargs["type"] == "agent_result"
        assert first_call_kwargs["task_id"] == "test123"
        content = first_call_kwargs["content"]
        assert "fixer-1" in content
        assert "fixer" in content
        assert "fixed" in content
        assert "Rebased cleanly" in content

    def test_fixer_failed_outcome_posts_agent_result_message(self, tmp_task_dir, mock_sdk):
        """Fixer 'failed' outcome posts an agent_result message."""
        task = {"id": "test123", "queue": "requires-intervention", "flow": "default"}
        mock_sdk.tasks.get.return_value = task

        inferred = {"outcome": "failed", "diagnosis": "Conflicts unresolvable"}

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value=inferred):
            mock_qu.get_sdk.return_value = mock_sdk

            from octopoid.scheduler import handle_fixer_result
            handle_fixer_result("test123", "fixer-1", tmp_task_dir)

        assert mock_sdk.messages.create.called
        first_call_kwargs = mock_sdk.messages.create.call_args_list[0][1]
        assert first_call_kwargs["type"] == "agent_result"
        content = first_call_kwargs["content"]
        assert "failed" in content
        assert "Conflicts unresolvable" in content


# ---------------------------------------------------------------------------
# Test: _post_agent_result_message unit tests
# ---------------------------------------------------------------------------

class TestPostAgentResultMessage:
    """Unit tests for the _post_agent_result_message helper."""

    def test_posts_with_correct_fields(self):
        """Message is posted with the correct sdk.messages.create arguments."""
        from octopoid.result_handler import _post_agent_result_message

        mock_sdk = MagicMock()
        result = {"outcome": "done"}
        _post_agent_result_message(mock_sdk, "task-abc", "impl-1", "implement", result)

        mock_sdk.messages.create.assert_called_once()
        call_kwargs = mock_sdk.messages.create.call_args[1]
        assert call_kwargs["task_id"] == "task-abc"
        assert call_kwargs["type"] == "agent_result"
        assert call_kwargs["from_actor"] == "agent"
        assert call_kwargs["to_actor"] == "human"

    def test_content_includes_agent_name_and_role(self):
        """Message content mentions agent_name and agent_role."""
        from octopoid.result_handler import _post_agent_result_message

        mock_sdk = MagicMock()
        _post_agent_result_message(mock_sdk, "task-abc", "gatekeeper-2", "gatekeeper", {"status": "success", "decision": "approve"})

        content = mock_sdk.messages.create.call_args[1]["content"]
        assert "gatekeeper-2" in content
        assert "gatekeeper" in content

    def test_implementer_outcome_in_content(self):
        """Implementer result: 'outcome' field appears in content."""
        from octopoid.result_handler import _post_agent_result_message

        mock_sdk = MagicMock()
        _post_agent_result_message(mock_sdk, "task-abc", "impl-1", "implement", {"outcome": "done"})

        content = mock_sdk.messages.create.call_args[1]["content"]
        assert "done" in content

    def test_gatekeeper_decision_in_content(self):
        """Gatekeeper result: 'decision' field appears in content."""
        from octopoid.result_handler import _post_agent_result_message

        mock_sdk = MagicMock()
        _post_agent_result_message(mock_sdk, "task-abc", "gk-1", "gatekeeper", {"status": "success", "decision": "reject"})

        content = mock_sdk.messages.create.call_args[1]["content"]
        assert "reject" in content

    def test_fixer_diagnosis_in_content(self):
        """Fixer result: 'diagnosis' field appears in content."""
        from octopoid.result_handler import _post_agent_result_message

        mock_sdk = MagicMock()
        _post_agent_result_message(mock_sdk, "task-abc", "fixer-1", "fixer", {
            "outcome": "fixed", "diagnosis": "Merge conflict in setup.py"
        })

        content = mock_sdk.messages.create.call_args[1]["content"]
        assert "Merge conflict in setup.py" in content

    def test_comment_excluded_from_json_summary(self):
        """The 'comment' field (stdout tail) is excluded from the JSON block in the message."""
        from octopoid.result_handler import _post_agent_result_message

        mock_sdk = MagicMock()
        long_comment = "X" * 500
        _post_agent_result_message(mock_sdk, "task-abc", "gk-1", "gatekeeper", {
            "status": "success", "decision": "approve", "comment": long_comment
        })

        content = mock_sdk.messages.create.call_args[1]["content"]
        # comment is excluded from the JSON block
        assert long_comment not in content
        # but decision is still present
        assert "approve" in content

    def test_sdk_exception_is_swallowed(self):
        """If sdk.messages.create raises, the error is logged but not re-raised."""
        from octopoid.result_handler import _post_agent_result_message

        mock_sdk = MagicMock()
        mock_sdk.messages.create.side_effect = RuntimeError("API error")

        # Should not raise
        _post_agent_result_message(mock_sdk, "task-abc", "impl-1", "implement", {"outcome": "done"})

    def test_reason_field_in_content(self):
        """Failed result with 'reason' field: reason appears in content."""
        from octopoid.result_handler import _post_agent_result_message

        mock_sdk = MagicMock()
        _post_agent_result_message(mock_sdk, "task-abc", "impl-1", "implement", {
            "outcome": "failed", "reason": "Could not compile"
        })

        content = mock_sdk.messages.create.call_args[1]["content"]
        assert "Could not compile" in content

    def test_message_field_in_content(self):
        """Inference failure with 'message' field: message appears in content."""
        from octopoid.result_handler import _post_agent_result_message

        mock_sdk = MagicMock()
        _post_agent_result_message(mock_sdk, "task-abc", "gk-1", "gatekeeper", {
            "status": "failure", "message": "Haiku timed out"
        })

        content = mock_sdk.messages.create.call_args[1]["content"]
        assert "Haiku timed out" in content
