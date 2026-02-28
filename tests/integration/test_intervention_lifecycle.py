"""Integration tests for the message-based intervention lifecycle.

Tests the intervention system against a real local server at localhost:9787.
Uses scoped_sdk for per-test isolation so no cleanup is needed between tests.

Covers:
- fail_task() sets needs_intervention=True (first failure) — task stays in queue
- fail_task() with needs_intervention already True → moves to failed (second failure)
- request_intervention() posts intervention_request message with correct actor fields
- intervention_request message contains parseable JSON context block
- handle_fixer_result() outcome=fixed clears needs_intervention and posts reply
- handle_fixer_result() outcome=failed moves task to failed terminal state

Run with a local test server on port 9787:
    ./tests/integration/bin/start-test-server.sh
"""

import json
import re
from pathlib import Path

import pytest

from tests.integration.flow_helpers import make_task_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_claimed_task(sdk, orchestrator_id: str, title: str = "Intervention test") -> str:
    """Create a task and claim it. Returns the task ID."""
    task_id = make_task_id()
    sdk.tasks.create(
        id=task_id,
        file_path=f".octopoid/tasks/{task_id}.md",
        title=title,
        role="implement",
        branch="main",
    )
    sdk.tasks.claim(
        orchestrator_id=orchestrator_id,
        agent_name="test-agent",
        role_filter="implement",
    )
    return task_id


def _get_intervention_messages(sdk, task_id: str) -> list:
    """Fetch all intervention_request messages for a task (to fixer actor)."""
    try:
        return sdk.messages.list(task_id=task_id, to_actor="fixer", type="intervention_request")
    except TypeError:
        # Fallback: list all messages and filter client-side
        all_messages = sdk.messages.list(task_id=task_id)
        return [
            m for m in all_messages
            if m.get("to_actor") == "fixer" and m.get("type") == "intervention_request"
        ]


def _get_reply_messages(sdk, task_id: str) -> list:
    """Fetch all intervention_reply messages for a task."""
    try:
        all_messages = sdk.messages.list(task_id=task_id)
        return [m for m in all_messages if m.get("type") == "intervention_reply"]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Tests: fail_task() first failure → sets needs_intervention=True
# ---------------------------------------------------------------------------


class TestFailTaskFirstFailure:
    """fail_task() first failure sets needs_intervention=True and posts intervention_request."""

    def test_sets_needs_intervention_true(self, scoped_sdk, orchestrator_id):
        """First failure: task's needs_intervention field is set to True on the server."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "First-failure test")

        from octopoid.tasks import fail_task
        fail_task(task_id, reason="step failed badly", source="test-source")

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        # Server stores booleans as integers (SQLite) — use truthiness check
        assert task.get("needs_intervention"), "needs_intervention must be truthy after first failure"

    def test_task_stays_in_claimed_queue(self, scoped_sdk, orchestrator_id):
        """First failure: task remains in claimed queue (no queue transition)."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "Queue-stay test")

        from octopoid.tasks import fail_task
        fail_task(task_id, reason="push failed", source="push-error")

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        # Task must NOT have moved to requires-intervention or any other queue
        assert task["queue"] == "claimed"

    def test_does_not_transition_to_requires_intervention_queue(self, scoped_sdk, orchestrator_id):
        """First failure: task must not be moved to the deprecated requires-intervention queue."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "No-queue-move test")

        from octopoid.tasks import fail_task
        fail_task(task_id, reason="rebase failed", source="rebase-error")

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] != "requires-intervention", (
            "fail_task must not transition to the deprecated requires-intervention queue"
        )

    def test_posts_intervention_request_message(self, scoped_sdk, orchestrator_id):
        """First failure: intervention_request message is posted to the fixer actor."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "Message-post test")

        from octopoid.tasks import fail_task
        fail_task(task_id, reason="create_pr failed", source="create_pr-error")

        messages = _get_intervention_messages(scoped_sdk, task_id)
        assert len(messages) >= 1, (
            "fail_task() must post at least one intervention_request message"
        )
        msg = messages[-1]
        assert msg["type"] == "intervention_request"
        assert msg["to_actor"] == "fixer"
        assert msg["from_actor"] == "scheduler"

    def test_intervention_request_message_contains_parseable_json_context(
        self, scoped_sdk, orchestrator_id
    ):
        """intervention_request message embeds a parseable JSON context block."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "JSON-context test")

        from octopoid.tasks import fail_task
        fail_task(task_id, reason="step failed for testing", source="test-step-error")

        messages = _get_intervention_messages(scoped_sdk, task_id)
        assert len(messages) >= 1

        content = messages[-1]["content"]
        match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
        assert match is not None, (
            "intervention_request message must contain a ```json``` code block"
        )
        ctx = json.loads(match.group(1))
        assert "previous_queue" in ctx
        assert "error_source" in ctx
        assert ctx["error_source"] == "test-step-error"
        assert "error_message" in ctx
        assert "step failed for testing" in ctx["error_message"]


# ---------------------------------------------------------------------------
# Tests: fail_task() second failure → terminal failed
# ---------------------------------------------------------------------------


class TestFailTaskSecondFailure:
    """fail_task() second failure (needs_intervention already True) moves to failed."""

    def test_second_failure_moves_task_to_failed_queue(self, scoped_sdk, orchestrator_id):
        """Second call to fail_task() when needs_intervention=True → queue=failed."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "Double-failure test")

        from octopoid.tasks import fail_task

        # First failure: sets needs_intervention=True
        fail_task(task_id, reason="original error", source="step-error")
        task = scoped_sdk.tasks.get(task_id)
        assert task.get("needs_intervention"), "First failure must set needs_intervention to truthy"

        # Second failure: fixer also failed → terminal state
        fail_task(task_id, reason="fixer could not fix it", source="fixer-failed")

        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "failed", (
            f"Second failure must move task to 'failed', got queue={task['queue']!r}"
        )

    def test_second_failure_clears_needs_intervention(self, scoped_sdk, orchestrator_id):
        """When task moves to failed, needs_intervention flag is cleared."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "Flag-cleared test")

        from octopoid.tasks import fail_task

        fail_task(task_id, reason="first error", source="step-error")
        fail_task(task_id, reason="second error", source="fixer-failed")

        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "failed"
        # needs_intervention should be falsy after moving to failed
        assert not task.get("needs_intervention"), (
            "needs_intervention must be cleared when task reaches terminal failed state"
        )


# ---------------------------------------------------------------------------
# Tests: request_intervention() message fields
# ---------------------------------------------------------------------------


class TestRequestIntervention:
    """request_intervention() posts intervention_request with correct actor fields."""

    def test_posts_message_with_correct_actors_and_type(self, scoped_sdk, orchestrator_id):
        """request_intervention posts from_actor=scheduler, to_actor=fixer, type=intervention_request."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "request_intervention test")

        from octopoid.tasks import request_intervention
        request_intervention(
            task_id,
            reason="push failed",
            source="push-error",
            previous_queue="claimed",
        )

        messages = _get_intervention_messages(scoped_sdk, task_id)
        assert len(messages) >= 1

        msg = messages[-1]
        assert msg["type"] == "intervention_request"
        assert msg["to_actor"] == "fixer"
        assert msg["from_actor"] == "scheduler"

    def test_sets_needs_intervention_true(self, scoped_sdk, orchestrator_id):
        """request_intervention() sets needs_intervention=True without moving the task."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "request_intervention flag test")

        from octopoid.tasks import request_intervention
        request_intervention(
            task_id,
            reason="rebase failed",
            source="rebase-error",
            previous_queue="claimed",
        )

        task = scoped_sdk.tasks.get(task_id)
        assert task.get("needs_intervention"), "needs_intervention must be truthy after request_intervention"
        assert task["queue"] != "requires-intervention"


# ---------------------------------------------------------------------------
# Tests: handle_fixer_result() outcome=fixed
# ---------------------------------------------------------------------------


class TestHandleFixerResultFixed:
    """handle_fixer_result() outcome=fixed clears needs_intervention and posts reply."""

    def _write_fixed_stdout(self, task_dir: Path) -> None:
        """Write stdout.log with content that the mock classifies as 'fixed'."""
        task_dir.mkdir(parents=True, exist_ok=True)
        # The mock_infer_result_from_stdout fixture matches: "fixed" | "resolved" | "fix applied"
        (task_dir / "stdout.log").write_text(
            "Successfully resolved the issue. The fix has been applied."
        )

    def test_fixed_outcome_clears_needs_intervention(
        self, scoped_sdk, orchestrator_id, tmp_path, monkeypatch
    ):
        """outcome=fixed clears needs_intervention=False on the server."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "Fixer-fixed test")

        # Put task into intervention state
        from octopoid.tasks import fail_task
        fail_task(task_id, reason="step failed", source="step-error")
        assert scoped_sdk.tasks.get(task_id).get("needs_intervention"), "Precondition: needs_intervention must be set"

        # Patch _resume_flow to avoid running actual git operations
        monkeypatch.setattr("octopoid.result_handler._resume_flow", lambda *a, **kw: None)

        task_dir = tmp_path / task_id
        self._write_fixed_stdout(task_dir)

        from octopoid.result_handler import handle_fixer_result
        result = handle_fixer_result(task_id, "test-fixer", task_dir)

        assert result is True
        task = scoped_sdk.tasks.get(task_id)
        assert not task.get("needs_intervention"), (
            "handle_fixer_result outcome=fixed must clear needs_intervention"
        )

    def test_fixed_outcome_posts_intervention_reply(
        self, scoped_sdk, orchestrator_id, tmp_path, monkeypatch
    ):
        """outcome=fixed posts an intervention_reply message to scheduler."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "Fixer-reply test")

        from octopoid.tasks import fail_task
        fail_task(task_id, reason="step failed", source="step-error")

        monkeypatch.setattr("octopoid.result_handler._resume_flow", lambda *a, **kw: None)

        task_dir = tmp_path / task_id
        self._write_fixed_stdout(task_dir)

        from octopoid.result_handler import handle_fixer_result
        handle_fixer_result(task_id, "test-fixer", task_dir)

        reply_messages = _get_reply_messages(scoped_sdk, task_id)
        assert len(reply_messages) >= 1, (
            "handle_fixer_result outcome=fixed must post an intervention_reply message"
        )
        reply = reply_messages[-1]
        assert reply["to_actor"] == "scheduler"

    def test_fixed_outcome_returns_true(
        self, scoped_sdk, orchestrator_id, tmp_path, monkeypatch
    ):
        """handle_fixer_result returns True (PID safe to remove) on fixed outcome."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "Fixer-returns-true test")

        from octopoid.tasks import fail_task
        fail_task(task_id, reason="step failed", source="step-error")

        monkeypatch.setattr("octopoid.result_handler._resume_flow", lambda *a, **kw: None)

        task_dir = tmp_path / task_id
        self._write_fixed_stdout(task_dir)

        from octopoid.result_handler import handle_fixer_result
        result = handle_fixer_result(task_id, "test-fixer", task_dir)

        assert result is True


# ---------------------------------------------------------------------------
# Tests: handle_fixer_result() outcome=failed
# ---------------------------------------------------------------------------


class TestHandleFixerResultFailed:
    """handle_fixer_result() outcome=failed moves task to terminal failed state."""

    def _write_failed_stdout(self, task_dir: Path) -> None:
        """Write stdout.log with content that the mock classifies as 'failed'."""
        task_dir.mkdir(parents=True, exist_ok=True)
        # The mock does NOT match: no "fixed", "resolved", or "fix applied"
        (task_dir / "stdout.log").write_text(
            "Could not complete the task. The issue is too complex to diagnose."
        )

    def test_failed_outcome_moves_task_to_failed_queue(
        self, scoped_sdk, orchestrator_id, tmp_path
    ):
        """outcome=failed moves task to the terminal failed queue."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "Fixer-failed test")

        # Put task into intervention state (needs_intervention=True)
        from octopoid.tasks import fail_task
        fail_task(task_id, reason="original step error", source="step-error")
        assert scoped_sdk.tasks.get(task_id).get("needs_intervention"), "Precondition: needs_intervention must be set"

        task_dir = tmp_path / task_id
        self._write_failed_stdout(task_dir)

        from octopoid.result_handler import handle_fixer_result
        result = handle_fixer_result(task_id, "test-fixer", task_dir)

        assert result is True
        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "failed", (
            f"handle_fixer_result outcome=failed must move task to failed, got {task['queue']!r}"
        )

    def test_failed_outcome_returns_true(
        self, scoped_sdk, orchestrator_id, tmp_path
    ):
        """handle_fixer_result returns True on failed outcome (PID safe to remove)."""
        task_id = _create_claimed_task(scoped_sdk, orchestrator_id, "Fixer-failed-returns-true test")

        from octopoid.tasks import fail_task
        fail_task(task_id, reason="original error", source="step-error")

        task_dir = tmp_path / task_id
        self._write_failed_stdout(task_dir)

        from octopoid.result_handler import handle_fixer_result
        result = handle_fixer_result(task_id, "test-fixer", task_dir)

        assert result is True
