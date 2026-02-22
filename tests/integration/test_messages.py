"""Integration tests for the Messages API.

Tests the full messages API against the real local test server at localhost:9787:

- POST /api/v1/messages — create a message
- GET /api/v1/messages?task_id=X — list with filters
- GET /api/v1/tasks/:id/messages — list for a specific task
- Append-only enforcement (no PUT/PATCH/DELETE)
- Scope isolation
- Agent result posting lifecycle
- Full orchestrator↔agent↔gatekeeper message thread

Agents use messages to post success and failure results (replacing result.json
on disk). The tests verify this primary production use case end-to-end.

Run prerequisites:
    cd submodules/server && npx wrangler dev --port 9787
"""

import json
import uuid

import pytest
import requests as http_requests

TEST_SERVER_URL = "http://localhost:9787"


def _task_id() -> str:
    return f"TASK-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMessagesBasics:
    """Core Messages API: create, list, filter, and append-only enforcement."""

    def test_create_message_returns_id_and_created_at(self, scoped_sdk):
        """POST /api/v1/messages creates a message and returns id and created_at."""
        task_id = _task_id()

        msg = scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            type="instruction",
            content="Implement the feature.",
            to_actor="implementer",
        )

        assert "id" in msg, "Message must have an id"
        assert "created_at" in msg, "Message must have created_at"
        assert msg["task_id"] == task_id
        assert msg["from_actor"] == "orchestrator"
        assert msg["to_actor"] == "implementer"
        assert msg["type"] == "instruction"
        assert msg["content"] == "Implement the feature."

    def test_get_messages_by_task_id_ordered(self, scoped_sdk):
        """GET /api/v1/messages?task_id=X returns messages ordered by created_at."""
        task_id = _task_id()

        msg1 = scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            type="instruction",
            content="First instruction.",
            to_actor="implementer",
        )
        msg2 = scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="implementer",
            type="result",
            content=json.dumps({"status": "success"}),
            to_actor="orchestrator",
        )
        msg3 = scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            type="instruction",
            content="Second instruction.",
            to_actor="gatekeeper",
        )

        messages = scoped_sdk.messages.list(task_id=task_id)

        assert len(messages) == 3
        msg_ids = [m["id"] for m in messages]
        assert msg_ids == [msg1["id"], msg2["id"], msg3["id"]], (
            "Messages must be ordered by created_at ascending"
        )

    def test_get_messages_by_to_actor(self, scoped_sdk):
        """GET /api/v1/messages?to_actor=X filters by target actor."""
        task_id = _task_id()

        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            type="instruction",
            content="For implementer",
            to_actor="implementer",
        )
        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            type="instruction",
            content="For gatekeeper",
            to_actor="gatekeeper",
        )
        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            type="instruction",
            content="Also for implementer",
            to_actor="implementer",
        )

        messages = scoped_sdk.messages.list(to_actor="implementer")

        assert len(messages) == 2
        for m in messages:
            assert m["to_actor"] == "implementer"

    def test_get_messages_by_type(self, scoped_sdk):
        """GET /api/v1/messages?type=X filters by message type."""
        task_id = _task_id()

        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            type="instruction",
            content="Do the work",
            to_actor="implementer",
        )
        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="implementer",
            type="result",
            content=json.dumps({"status": "success"}),
            to_actor="orchestrator",
        )
        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            type="feedback",
            content="Looks good",
            to_actor="implementer",
        )

        results = scoped_sdk.messages.list(task_id=task_id, type="result")

        assert len(results) == 1
        assert results[0]["type"] == "result"

    def test_get_task_messages_endpoint(self, scoped_sdk):
        """GET /api/v1/tasks/:id/messages returns messages for that task."""
        task_id = _task_id()

        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            type="instruction",
            content="Work on this task.",
            to_actor="implementer",
        )
        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="implementer",
            type="result",
            content=json.dumps({"status": "success"}),
            to_actor="orchestrator",
        )

        messages = scoped_sdk.messages.list_for_task(task_id)

        assert len(messages) == 2
        for m in messages:
            assert m["task_id"] == task_id

    def test_append_only_no_put_patch_delete(self, scoped_sdk):
        """Append-only: PUT/PATCH/DELETE on a message returns 404 or 405."""
        task_id = _task_id()

        msg = scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            type="instruction",
            content="Test message.",
        )
        msg_id = msg["id"]
        scope = scoped_sdk.scope

        for method in ("PUT", "PATCH", "DELETE"):
            resp = http_requests.request(
                method,
                f"{TEST_SERVER_URL}/api/v1/messages/{msg_id}",
                params={"scope": scope},
                json={"content": "modified"},
            )
            assert resp.status_code in (404, 405), (
                f"{method} /api/v1/messages/:id should be 404 or 405, "
                f"got {resp.status_code}"
            )

    def test_scope_isolation(self, test_server_url):
        """Messages in one scope are not visible in another scope."""
        from octopoid_sdk import OctopoidSDK

        scope_a = f"test-scope-a-{uuid.uuid4().hex[:8]}"
        scope_b = f"test-scope-b-{uuid.uuid4().hex[:8]}"

        sdk_a = OctopoidSDK(server_url=test_server_url, scope=scope_a)
        sdk_b = OctopoidSDK(server_url=test_server_url, scope=scope_b)

        try:
            task_id = _task_id()

            # Create a message in scope A
            sdk_a.messages.create(
                task_id=task_id,
                from_actor="orchestrator",
                type="instruction",
                content="Scope A message",
            )

            # Scope B must not see it
            messages_b = sdk_b.messages.list(task_id=task_id)
            assert len(messages_b) == 0, (
                "Messages from scope A must not be visible in scope B"
            )

            # Scope A sees it
            messages_a = sdk_a.messages.list(task_id=task_id)
            assert len(messages_a) == 1
        finally:
            sdk_a.close()
            sdk_b.close()


class TestAgentResultPosting:
    """Verify the primary production use case: agent posts results via messages."""

    def test_agent_posts_success_result(self, scoped_sdk):
        """Agent posts a 'result' message with status 'success' — retrievable by task_id and type."""
        task_id = _task_id()

        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="implementer",
            type="result",
            content=json.dumps({"status": "success", "commits": 3}),
            to_actor="orchestrator",
        )

        results = scoped_sdk.messages.list(task_id=task_id, type="result")
        assert len(results) == 1
        content = json.loads(results[0]["content"])
        assert content["status"] == "success"

    def test_agent_posts_failure_result(self, scoped_sdk):
        """Agent posts a 'result' message with status 'failure' including error details."""
        task_id = _task_id()

        error_content = json.dumps({
            "status": "failure",
            "reason": "Tests failed: 3 assertions failed",
            "error_details": "AssertionError: expected 200, got 500",
        })

        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="implementer",
            type="result",
            content=error_content,
            to_actor="orchestrator",
        )

        results = scoped_sdk.messages.list(task_id=task_id, type="result")
        assert len(results) == 1
        content = json.loads(results[0]["content"])
        assert content["status"] == "failure"
        assert "reason" in content

    def test_multiple_results_preserved_in_order(self, scoped_sdk):
        """Multiple result messages on the same task are all preserved in order."""
        task_id = _task_id()

        # First attempt: failure
        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="implementer",
            type="result",
            content=json.dumps({"status": "failure", "attempt": 1}),
            to_actor="orchestrator",
        )

        # Orchestrator sends retry instruction
        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            type="instruction",
            content="Please fix the test failures and retry.",
            to_actor="implementer",
        )

        # Second attempt: success
        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="implementer",
            type="result",
            content=json.dumps({"status": "success", "attempt": 2}),
            to_actor="orchestrator",
        )

        # All messages preserved
        all_messages = scoped_sdk.messages.list(task_id=task_id)
        assert len(all_messages) == 3

        # Both result messages preserved in order
        results = scoped_sdk.messages.list(task_id=task_id, type="result")
        assert len(results) == 2
        contents = [json.loads(r["content"]) for r in results]
        assert contents[0]["attempt"] == 1
        assert contents[1]["attempt"] == 2

    def test_result_content_has_status_field(self, scoped_sdk):
        """Result message content is valid JSON with at least a 'status' field."""
        task_id = _task_id()

        msg = scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="implementer",
            type="result",
            content=json.dumps({"status": "success"}),
            to_actor="orchestrator",
        )

        content = json.loads(msg["content"])
        assert "status" in content


class TestFullLifecycleThread:
    """Test the full orchestrator↔agent↔gatekeeper message thread."""

    def test_full_message_thread(self, scoped_sdk):
        """Full thread: orchestrator->implementer instruction, implementer->orchestrator
        success result, orchestrator->gatekeeper review instruction,
        gatekeeper->orchestrator rejection result, orchestrator->implementer
        retry instruction with feedback — returned in chronological order.
        """
        task_id = _task_id()

        # 1. Orchestrator sends instruction to implementer
        m1 = scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            to_actor="implementer",
            type="instruction",
            content="Implement the new feature per the task description.",
        )

        # 2. Implementer posts success result
        m2 = scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="implementer",
            to_actor="orchestrator",
            type="result",
            content=json.dumps({"status": "success", "commits": 2}),
        )

        # 3. Orchestrator sends review instruction to gatekeeper
        m3 = scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            to_actor="gatekeeper",
            type="instruction",
            content="Please review PR #42.",
        )

        # 4. Gatekeeper posts rejection result
        m4 = scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="gatekeeper",
            to_actor="orchestrator",
            type="result",
            content=json.dumps({"status": "failure", "reason": "Missing tests"}),
        )

        # 5. Orchestrator sends retry instruction with feedback to implementer
        m5 = scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            to_actor="implementer",
            type="instruction",
            content=(
                "Please add tests for the new feature. "
                "Gatekeeper rejected: Missing tests."
            ),
        )

        # Full thread in chronological order
        thread = scoped_sdk.messages.list(task_id=task_id)
        assert len(thread) == 5, f"Expected 5 messages, got {len(thread)}"

        thread_ids = [m["id"] for m in thread]
        assert thread_ids == [m1["id"], m2["id"], m3["id"], m4["id"], m5["id"]], (
            "Thread must be returned in chronological order"
        )

    def test_filter_by_to_actor_within_thread(self, scoped_sdk):
        """Filtering by to_actor within a thread returns only that actor's messages."""
        task_id = _task_id()

        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            to_actor="implementer",
            type="instruction",
            content="Do the implementation.",
        )
        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="implementer",
            to_actor="orchestrator",
            type="result",
            content=json.dumps({"status": "success"}),
        )
        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            to_actor="gatekeeper",
            type="instruction",
            content="Review the PR.",
        )
        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="gatekeeper",
            to_actor="orchestrator",
            type="result",
            content=json.dumps({"status": "failure", "reason": "Missing tests"}),
        )
        scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            to_actor="implementer",
            type="instruction",
            content="Add tests.",
        )

        # Filter: only messages to implementer
        implementer_msgs = scoped_sdk.messages.list(
            task_id=task_id, to_actor="implementer"
        )
        assert len(implementer_msgs) == 2
        for m in implementer_msgs:
            assert m["to_actor"] == "implementer"

        # Filter: only messages to gatekeeper
        gatekeeper_msgs = scoped_sdk.messages.list(
            task_id=task_id, to_actor="gatekeeper"
        )
        assert len(gatekeeper_msgs) == 1
        assert gatekeeper_msgs[0]["from_actor"] == "orchestrator"


class TestEdgeCases:
    """Edge cases for the messages API."""

    def test_broadcast_message_no_to_actor(self, scoped_sdk):
        """Broadcast messages (no to_actor) are created successfully."""
        task_id = _task_id()

        msg = scoped_sdk.messages.create(
            task_id=task_id,
            from_actor="orchestrator",
            type="status",
            content="Task started.",
            # No to_actor — broadcast
        )

        assert "id" in msg
        assert msg.get("to_actor") is None

        # Retrievable by task_id
        messages = scoped_sdk.messages.list(task_id=task_id)
        assert len(messages) == 1
        assert messages[0]["id"] == msg["id"]

    def test_empty_content_fails_validation(self, scoped_sdk):
        """Empty content field is rejected by the server."""
        task_id = _task_id()

        with pytest.raises(Exception) as exc_info:
            scoped_sdk.messages.create(
                task_id=task_id,
                from_actor="orchestrator",
                type="instruction",
                content="",  # Empty content should fail validation
            )

        exc = exc_info.value
        if hasattr(exc, "response"):
            assert exc.response.status_code in (400, 422), (
                f"Expected 400 or 422 for empty content, got {exc.response.status_code}"
            )
