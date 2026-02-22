"""Tests for the message dispatcher (orchestrator/message_dispatcher.py).

Covers:
- State loading/saving
- Stuck message detection and reset
- Skipping already-done/failed/processing messages
- Serial processing (one message per tick)
- Success path: posts worker_result, marks done
- Failure path: posts error, marks failed
- Build agent prompt includes message content
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message(
    msg_id: str = "msg-001",
    content: str = "archive draft 80 as superseded",
    task_id: str = "80",
    to_actor: str = "agent",
    msg_type: str = "action_command",
) -> dict:
    return {
        "id": msg_id,
        "task_id": task_id,
        "from_actor": "human",
        "to_actor": to_actor,
        "type": msg_type,
        "content": content,
        "created_at": "2026-02-22T15:00:00.000Z",
    }


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


class TestStateIO:
    def test_load_state_missing_file_returns_empty(self, tmp_path):
        state_path = tmp_path / "state.json"
        with patch(
            "orchestrator.message_dispatcher._get_state_path",
            return_value=state_path,
        ):
            from orchestrator.message_dispatcher import _load_state
            state = _load_state()

        assert state == {"done": [], "failed": [], "processing": {}}

    def test_load_state_corrupt_file_returns_empty(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text("NOT JSON")
        with patch(
            "orchestrator.message_dispatcher._get_state_path",
            return_value=state_path,
        ):
            from orchestrator.message_dispatcher import _load_state
            state = _load_state()

        assert state == {"done": [], "failed": [], "processing": {}}

    def test_save_and_load_roundtrip(self, tmp_path):
        state_path = tmp_path / "state.json"
        payload = {
            "done": ["msg-001"],
            "failed": ["msg-002"],
            "processing": {"msg-003": {"started_at": "2026-01-01T00:00:00+00:00"}},
        }
        with patch(
            "orchestrator.message_dispatcher._get_state_path",
            return_value=state_path,
        ):
            from orchestrator.message_dispatcher import _save_state, _load_state
            _save_state(payload)
            result = _load_state()

        assert result == payload


# ---------------------------------------------------------------------------
# Build agent prompt
# ---------------------------------------------------------------------------


class TestBuildAgentPrompt:
    def test_includes_message_content(self, tmp_path):
        """Prompt must contain the command from the message content."""
        gi_path = tmp_path / "global-instructions.md"
        gi_path.write_text("# Global Instructions\nDo good work.")

        msg = _make_message(content="archive draft 80 as superseded", msg_id="msg-001")

        with patch(
            "orchestrator.message_dispatcher.get_global_instructions_path",
            return_value=gi_path,
        ):
            from orchestrator.message_dispatcher import _build_agent_prompt
            prompt = _build_agent_prompt(msg)

        assert "archive draft 80 as superseded" in prompt
        assert "msg-001" in prompt
        assert "Global Instructions" in prompt

    def test_includes_constraints(self, tmp_path):
        """Prompt must mention the allowed/not-allowed constraint."""
        gi_path = tmp_path / "gi.md"
        gi_path.write_text("")
        msg = _make_message()

        with patch(
            "orchestrator.message_dispatcher.get_global_instructions_path",
            return_value=gi_path,
        ):
            from orchestrator.message_dispatcher import _build_agent_prompt
            prompt = _build_agent_prompt(msg)

        assert "project-management/" in prompt
        assert "Git operations" in prompt

    def test_works_without_global_instructions(self, tmp_path):
        """Missing global-instructions.md is handled gracefully."""
        gi_path = tmp_path / "nonexistent.md"  # does not exist
        msg = _make_message(content="do something")

        with patch(
            "orchestrator.message_dispatcher.get_global_instructions_path",
            return_value=gi_path,
        ):
            from orchestrator.message_dispatcher import _build_agent_prompt
            prompt = _build_agent_prompt(msg)

        assert "do something" in prompt


# ---------------------------------------------------------------------------
# dispatch_action_messages — main integration scenarios
# ---------------------------------------------------------------------------


class TestDispatchActionMessages:
    """Tests for the main dispatch_action_messages() function."""

    def _mock_sdk(self, messages: list) -> MagicMock:
        sdk = MagicMock()
        sdk.messages.list.return_value = messages
        return sdk

    def test_no_messages_returns_early(self, tmp_path):
        """When the server has no action_command messages, nothing happens."""
        state_path = tmp_path / "state.json"
        sdk = self._mock_sdk([])

        with (
            patch("orchestrator.message_dispatcher.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.message_dispatcher._get_state_path", return_value=state_path),
            patch("orchestrator.message_dispatcher._run_action_agent") as mock_run,
            patch("orchestrator.message_dispatcher.find_parent_project", return_value=tmp_path),
        ):
            from orchestrator.message_dispatcher import dispatch_action_messages
            dispatch_action_messages()

        mock_run.assert_not_called()
        sdk.messages.create.assert_not_called()

    def test_skips_done_messages(self, tmp_path):
        """Messages already in done list are not re-processed."""
        msg = _make_message(msg_id="msg-001")
        state_path = tmp_path / "state.json"
        # Pre-populate state with msg-001 done
        state_path.write_text(json.dumps({"done": ["msg-001"], "failed": [], "processing": {}}))

        sdk = self._mock_sdk([msg])

        with (
            patch("orchestrator.message_dispatcher.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.message_dispatcher._get_state_path", return_value=state_path),
            patch("orchestrator.message_dispatcher._run_action_agent") as mock_run,
            patch("orchestrator.message_dispatcher.find_parent_project", return_value=tmp_path),
        ):
            from orchestrator.message_dispatcher import dispatch_action_messages
            dispatch_action_messages()

        mock_run.assert_not_called()

    def test_skips_failed_messages(self, tmp_path):
        """Messages already in failed list are not re-processed."""
        msg = _make_message(msg_id="msg-002")
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"done": [], "failed": ["msg-002"], "processing": {}}))

        sdk = self._mock_sdk([msg])

        with (
            patch("orchestrator.message_dispatcher.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.message_dispatcher._get_state_path", return_value=state_path),
            patch("orchestrator.message_dispatcher._run_action_agent") as mock_run,
            patch("orchestrator.message_dispatcher.find_parent_project", return_value=tmp_path),
        ):
            from orchestrator.message_dispatcher import dispatch_action_messages
            dispatch_action_messages()

        mock_run.assert_not_called()

    def test_success_path(self, tmp_path):
        """Successful agent execution marks message done and posts worker_result."""
        msg = _make_message(msg_id="msg-003", task_id="80")
        state_path = tmp_path / "state.json"
        gi_path = tmp_path / "gi.md"
        gi_path.write_text("")

        sdk = self._mock_sdk([msg])

        with (
            patch("orchestrator.message_dispatcher.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.message_dispatcher._get_state_path", return_value=state_path),
            patch(
                "orchestrator.message_dispatcher.get_global_instructions_path",
                return_value=gi_path,
            ),
            patch(
                "orchestrator.message_dispatcher._run_action_agent",
                return_value=(True, "Draft archived successfully."),
            ),
            patch("orchestrator.message_dispatcher.find_parent_project", return_value=tmp_path),
        ):
            from orchestrator.message_dispatcher import dispatch_action_messages
            dispatch_action_messages()

        # worker_result posted to human inbox
        sdk.messages.create.assert_called_once_with(
            task_id="80",
            from_actor="agent",
            to_actor="human",
            type="worker_result",
            content="Draft archived successfully.",
        )

        # State updated: done
        state = json.loads(state_path.read_text())
        assert "msg-003" in state["done"]
        assert "msg-003" not in state.get("failed", [])

    def test_failure_path(self, tmp_path):
        """Failed agent execution marks message failed and posts error to human."""
        msg = _make_message(msg_id="msg-004", task_id="81")
        state_path = tmp_path / "state.json"
        gi_path = tmp_path / "gi.md"
        gi_path.write_text("")

        sdk = self._mock_sdk([msg])

        with (
            patch("orchestrator.message_dispatcher.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.message_dispatcher._get_state_path", return_value=state_path),
            patch(
                "orchestrator.message_dispatcher.get_global_instructions_path",
                return_value=gi_path,
            ),
            patch(
                "orchestrator.message_dispatcher._run_action_agent",
                return_value=(False, "Exit code 1: something went wrong"),
            ),
            patch("orchestrator.message_dispatcher.find_parent_project", return_value=tmp_path),
        ):
            from orchestrator.message_dispatcher import dispatch_action_messages
            dispatch_action_messages()

        # Error posted to human inbox
        sdk.messages.create.assert_called_once()
        call_kwargs = sdk.messages.create.call_args
        assert call_kwargs.kwargs["to_actor"] == "human"
        assert call_kwargs.kwargs["type"] == "worker_result"
        assert "Action failed" in call_kwargs.kwargs["content"]

        # State updated: failed
        state = json.loads(state_path.read_text())
        assert "msg-004" in state["failed"]
        assert "msg-004" not in state.get("done", [])

    def test_serial_one_message_per_tick(self, tmp_path):
        """Only one message is processed per call (serial)."""
        messages = [
            _make_message(msg_id="msg-010"),
            _make_message(msg_id="msg-011"),
        ]
        state_path = tmp_path / "state.json"
        gi_path = tmp_path / "gi.md"
        gi_path.write_text("")

        sdk = self._mock_sdk(messages)

        with (
            patch("orchestrator.message_dispatcher.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.message_dispatcher._get_state_path", return_value=state_path),
            patch(
                "orchestrator.message_dispatcher.get_global_instructions_path",
                return_value=gi_path,
            ),
            patch(
                "orchestrator.message_dispatcher._run_action_agent",
                return_value=(True, "done"),
            ) as mock_run,
            patch("orchestrator.message_dispatcher.find_parent_project", return_value=tmp_path),
        ):
            from orchestrator.message_dispatcher import dispatch_action_messages
            dispatch_action_messages()

        # Only one agent spawned
        assert mock_run.call_count == 1

        # Only first message processed
        state = json.loads(state_path.read_text())
        assert "msg-010" in state["done"]
        assert "msg-011" not in state["done"]

    def test_stuck_message_marked_failed(self, tmp_path):
        """Messages stuck in processing > STUCK_THRESHOLD_SECONDS are marked failed."""
        msg = _make_message(msg_id="msg-020")
        state_path = tmp_path / "state.json"

        # Pre-populate with msg-020 stuck in processing (started 10 minutes ago)
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        state_path.write_text(json.dumps({
            "done": [],
            "failed": [],
            "processing": {
                "msg-020": {"started_at": old_time, "content": "archive draft"},
            },
        }))

        gi_path = tmp_path / "gi.md"
        gi_path.write_text("")
        sdk = self._mock_sdk([msg])

        with (
            patch("orchestrator.message_dispatcher.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.message_dispatcher._get_state_path", return_value=state_path),
            patch(
                "orchestrator.message_dispatcher.get_global_instructions_path",
                return_value=gi_path,
            ),
            patch(
                "orchestrator.message_dispatcher._run_action_agent",
                return_value=(True, "done"),
            ) as mock_run,
            patch("orchestrator.message_dispatcher.find_parent_project", return_value=tmp_path),
        ):
            from orchestrator.message_dispatcher import dispatch_action_messages
            dispatch_action_messages()

        # Stuck message moved to failed
        state = json.loads(state_path.read_text())
        assert "msg-020" in state["failed"]
        assert "msg-020" not in state.get("processing", {})

        # Error posted to human — content uses original message content (not truncated state)
        sdk.messages.create.assert_called_once()
        call_kwargs = sdk.messages.create.call_args.kwargs
        assert call_kwargs["task_id"] == "80"
        assert call_kwargs["to_actor"] == "human"
        assert call_kwargs["type"] == "worker_result"
        assert "stuck/timeout" in call_kwargs["content"]
        # Original message content ("archive draft 80 as superseded") should appear
        assert "archive draft 80 as superseded" in call_kwargs["content"]

    def test_sdk_list_failure_returns_early(self, tmp_path):
        """SDK failure on messages.list does not crash the scheduler."""
        sdk = MagicMock()
        sdk.messages.list.side_effect = Exception("network error")
        state_path = tmp_path / "state.json"

        with (
            patch("orchestrator.message_dispatcher.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.message_dispatcher._get_state_path", return_value=state_path),
            patch("orchestrator.message_dispatcher._run_action_agent") as mock_run,
            patch("orchestrator.message_dispatcher.find_parent_project", return_value=tmp_path),
        ):
            from orchestrator.message_dispatcher import dispatch_action_messages
            dispatch_action_messages()

        mock_run.assert_not_called()
