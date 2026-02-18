"""Integration tests for queue_utils SDK interactions.

These tests replace mock-based tests for operations that exercise real SDK calls.
They run against the local test server at localhost:9787.

Isolation: all tests use clean_tasks (deletes all tasks before/after each test)
so list and claim operations have predictable state.

These tests verify the real API contract — schema changes, missing endpoints,
and validation failures are caught here rather than silently passing mocked tests.
"""

import uuid

import pytest


def _task(sdk, task_id, title="Test Task", role="implement"):
    """Create a task with required fields."""
    return sdk.tasks.create(
        id=task_id,
        file_path=f"/tmp/{task_id}.md",
        title=title,
        role=role,
        branch="main",
    )


class TestGetAndUpdateTask:
    """Test get/update/retry by task ID."""

    def test_get_task_by_id(self, sdk, clean_tasks):
        """Getting a task by ID returns the correct task."""
        task_id = f"GET-{uuid.uuid4().hex[:8]}"
        _task(sdk, task_id, title="Get Test")

        fetched = sdk.tasks.get(task_id)

        assert fetched is not None
        assert fetched["id"] == task_id
        assert fetched["queue"] == "incoming"
        assert fetched["role"] == "implement"

    def test_get_nonexistent_task_returns_none(self, sdk, clean_tasks):
        """Getting a non-existent task ID returns None."""
        result = sdk.tasks.get("NONEXISTENT-task-xyz-0000")
        assert result is None

    def test_update_task_to_failed(self, sdk, clean_tasks):
        """Updating a task's queue to failed is reflected immediately."""
        task_id = f"FAIL-{uuid.uuid4().hex[:8]}"
        _task(sdk, task_id, title="Fail Test")

        result = sdk.tasks.update(task_id, queue="failed")

        assert result["id"] == task_id
        assert result["queue"] == "failed"

        fetched = sdk.tasks.get(task_id)
        assert fetched["queue"] == "failed"

    def test_retry_failed_task(self, sdk, clean_tasks):
        """A failed task can be moved back to incoming."""
        task_id = f"RETRY-{uuid.uuid4().hex[:8]}"
        _task(sdk, task_id, title="Retry Test")

        sdk.tasks.update(task_id, queue="failed")
        fetched = sdk.tasks.get(task_id)
        assert fetched["queue"] == "failed"

        result = sdk.tasks.update(
            task_id, queue="incoming", claimed_by=None, claimed_at=None
        )

        assert result["id"] == task_id
        assert result["queue"] == "incoming"


class TestQueueCounting:
    """Test list and count operations."""

    def test_count_queue_empty(self, sdk, clean_tasks):
        """An empty queue returns zero tasks."""
        tasks = sdk.tasks.list(queue="incoming")
        assert len(tasks) == 0

    def test_count_queue_with_tasks(self, sdk, clean_tasks):
        """Creating tasks increments the queue count."""
        _task(sdk, "count-a", title="Count A")
        _task(sdk, "count-b", title="Count B")

        tasks = sdk.tasks.list(queue="incoming")
        assert len(tasks) == 2

    def test_list_all_queues_separate(self, sdk, orchestrator_id, clean_tasks):
        """Tasks in different queues are listed separately."""
        _task(sdk, "list-001", title="Task 1")
        _task(sdk, "list-002", title="Task 2")

        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )

        incoming = sdk.tasks.list(queue="incoming")
        claimed = sdk.tasks.list(queue="claimed")

        assert len(incoming) == 1
        assert len(claimed) == 1


class TestClaimBehavior:
    """Test claim() behavior."""

    def test_claim_no_tasks_returns_none(self, sdk, orchestrator_id, clean_tasks):
        """Claim returns None when no tasks are available."""
        result = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert result is None

    def test_claim_moves_task_to_claimed_queue(self, sdk, orchestrator_id, clean_tasks):
        """Claiming a task changes its queue to 'claimed'."""
        _task(sdk, "claim-001", title="Claim Test Task")

        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )

        assert claimed is not None
        assert claimed["id"] == "claim-001"
        assert claimed["queue"] == "claimed"
        assert claimed["claimed_by"] == "test-agent"

    def test_claim_with_role_filter(self, sdk, orchestrator_id, clean_tasks):
        """Claim with role_filter returns only tasks of matching role."""
        _task(sdk, "impl-001", title="Implement Task", role="implement")
        _task(sdk, "breakdown-001", title="Breakdown Task", role="breakdown")

        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="implementer",
            role_filter="implement",
        )

        assert claimed is not None
        assert claimed["role"] == "implement"
        assert claimed["id"] == "impl-001"

    def test_claim_passes_agent_name(self, sdk, orchestrator_id, clean_tasks):
        """Agent name is stored on the claimed task."""
        _task(sdk, "agent-name-001", title="Agent Name Test")

        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="my-specific-agent",
            role_filter="implement",
        )

        assert claimed is not None
        assert claimed["claimed_by"] == "my-specific-agent"


class TestTaskLifecycle:
    """Test full task lifecycle."""

    def test_claim_submit_accept(self, sdk, orchestrator_id, clean_tasks):
        """Full lifecycle: incoming → claimed → provisional → done."""
        _task(sdk, "lifecycle-001", title="Lifecycle Test")

        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed is not None
        assert claimed["id"] == "lifecycle-001"
        assert claimed["queue"] == "claimed"

        submitted = sdk.tasks.submit(
            task_id="lifecycle-001",
            commits_count=5,
            turns_used=30,
            execution_notes="5 commits. 30 turns.",
        )
        assert submitted["id"] == "lifecycle-001"
        assert submitted["queue"] == "provisional"

        accepted = sdk.tasks.accept("lifecycle-001", accepted_by="complete_task")
        assert accepted["id"] == "lifecycle-001"
        assert accepted["queue"] == "done"

    def test_submit_records_commits_and_turns(self, sdk, orchestrator_id, clean_tasks):
        """Submit records commits_count and turns_used accurately."""
        _task(sdk, "submit-meta-001", title="Submit Metadata Test")

        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )

        result = sdk.tasks.submit(
            task_id="submit-meta-001",
            commits_count=7,
            turns_used=42,
        )

        assert result["commits_count"] == 7
        assert result["turns_used"] == 42

    def test_reject_returns_to_incoming(self, sdk, orchestrator_id, clean_tasks):
        """Rejecting a provisional task moves it back to incoming."""
        _task(sdk, "reject-001", title="Reject Test")

        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )

        sdk.tasks.submit(task_id="reject-001", commits_count=1, turns_used=5)

        result = sdk.tasks.reject(
            task_id="reject-001",
            reason="Tests failed — needs revision",
            rejected_by="gatekeeper-1",
        )

        assert result["id"] == "reject-001"
        assert result["queue"] == "incoming"

    def test_reject_increments_rejection_count(self, sdk, orchestrator_id, clean_tasks):
        """Rejecting a task increments its rejection_count."""
        _task(sdk, "reject-count-001", title="Rejection Count Test")

        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        sdk.tasks.submit(task_id="reject-count-001", commits_count=1, turns_used=5)

        sdk.tasks.reject(
            task_id="reject-count-001",
            reason="Incomplete implementation",
            rejected_by="gatekeeper",
        )

        fetched = sdk.tasks.get("reject-count-001")
        assert fetched is not None
        assert fetched.get("rejection_count", 0) >= 1
