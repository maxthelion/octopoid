"""Integration tests for task content after claiming.

Verifies that the orchestrator's claim_task() function returns task content
from the server's content field. The server is the single source of truth
for task content — no local files are read.
"""

from unittest.mock import patch

import pytest


TASK_CONTENT = """\
# [TASK-claim-content-001] Test task with real content

ROLE: implement
PRIORITY: P1
BRANCH: main

## Context

The widget factory produces widgets too slowly.

## Acceptance Criteria

- [ ] Widget throughput increased by 2x
- [ ] No regression in widget quality
- [ ] Tests pass
"""


class TestClaimReturnsContent:
    """After claiming, task dict must include content from the server."""

    def test_claim_task_includes_server_content(self, sdk, orchestrator_id, clean_tasks):
        """claim_task() must return content from the server's content field.

        The server stores the full task content. The orchestrator reads it
        directly from the claim response — no filesystem reads needed.
        """
        # Register the task with the API including content
        sdk.tasks.create(
            id="claim-content-001",
            file_path="TASK-claim-content-001.md",
            title="Test task with real content",
            role="implement",
            priority="P1",
            branch="main",
            content=TASK_CONTENT,
        )

        import orchestrator.queue_utils as qu

        with patch.object(qu, "get_orchestrator_id", return_value=orchestrator_id):
            task = qu.claim_task(role_filter="implement", agent_name="test-agent")

            # Fundamental assertions
            assert task is not None, "claim_task returned None — no task claimed"
            assert task["id"] == "claim-content-001"

            # THE KEY ASSERTION: content must be populated from the server
            assert "content" in task, (
                "claim_task() did not return 'content'. "
                "The server must return content in the claim response."
            )
            assert len(task["content"]) > 0, "task content is empty string"
            assert "widget factory" in task["content"], (
                f"task content doesn't contain expected text. Got: {task['content'][:200]}"
            )

    def test_claim_task_with_no_content_returns_empty(self, sdk, orchestrator_id, clean_tasks):
        """claim_task() with a task that has no content returns None/empty content.

        Tasks created without content should have None or empty string for content.
        The guard will catch this and fail the task.
        """
        # Register task without content field
        sdk.tasks.create(
            id="claim-content-003",
            file_path="TASK-claim-content-003.md",
            title="Task with no content",
            role="implement",
            priority="P1",
            branch="main",
        )

        import orchestrator.queue_utils as qu

        with patch.object(qu, "get_orchestrator_id", return_value=orchestrator_id):
            task = qu.claim_task(role_filter="implement", agent_name="test-agent")

            assert task is not None, "claim_task returned None — no task claimed"
            assert task["id"] == "claim-content-003"
            # Content should be None or empty (no content sent on create)
            content = task.get("content")
            assert not content, (
                f"Expected empty/None content for task with no content field, got: {content!r}"
            )
