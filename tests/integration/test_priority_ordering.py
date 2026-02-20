"""Integration tests for task priority ordering.

Tests that the claim endpoint returns tasks in priority order (P0 before P1 before P2),
and that tasks with the same priority are returned in FIFO (creation) order.
"""

import pytest

TEST_BRANCH = "feature/client-server-architecture"


class TestPriorityOrdering:
    """Test that claim returns tasks in priority order."""

    def test_priority_ordering_p0_before_p1_before_p2(self, scoped_sdk, orchestrator_id):
        """Claim returns P0 before P1 before P2, regardless of creation order.

        Creates tasks in P2, P0, P1 order (intentionally scrambled), then asserts
        that claims come back in strict priority order: P0 → P1 → P2.
        """
        # Create tasks in P2, P0, P1 order (deliberately out of priority order)
        scoped_sdk.tasks.create(
            id="priority-p2",
            file_path="/tmp/priority-p2.md",
            title="P2 Task",
            role="implement",
            priority="P2",
            branch=TEST_BRANCH,
        )
        scoped_sdk.tasks.create(
            id="priority-p0",
            file_path="/tmp/priority-p0.md",
            title="P0 Task",
            role="implement",
            priority="P0",
            branch=TEST_BRANCH,
        )
        scoped_sdk.tasks.create(
            id="priority-p1",
            file_path="/tmp/priority-p1.md",
            title="P1 Task",
            role="implement",
            priority="P1",
            branch=TEST_BRANCH,
        )

        # First claim must be the P0 task
        first = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-priority-agent",
            role_filter="implement",
        )
        assert first is not None
        assert first["priority"] == "P0", (
            f"Expected P0 first, got {first['priority']} (id={first['id']})"
        )

        # Second claim must be the P1 task
        second = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-priority-agent",
            role_filter="implement",
        )
        assert second is not None
        assert second["priority"] == "P1", (
            f"Expected P1 second, got {second['priority']} (id={second['id']})"
        )

        # Third claim must be the P2 task
        third = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-priority-agent",
            role_filter="implement",
        )
        assert third is not None
        assert third["priority"] == "P2", (
            f"Expected P2 third, got {third['priority']} (id={third['id']})"
        )

    def test_same_priority_fifo_order(self, scoped_sdk, orchestrator_id):
        """Tasks with the same priority are claimed in creation (FIFO) order.

        Creates 3 P1 tasks in sequence, then asserts that claim returns them
        in the same order they were created.
        """
        task_ids = ["fifo-task-1", "fifo-task-2", "fifo-task-3"]
        for task_id in task_ids:
            scoped_sdk.tasks.create(
                id=task_id,
                file_path=f"/tmp/{task_id}.md",
                title=f"FIFO Task {task_id}",
                role="implement",
                priority="P1",
                branch=TEST_BRANCH,
            )

        # Claim all 3 and verify FIFO order
        claimed_ids = []
        for _ in range(3):
            task = scoped_sdk.tasks.claim(
                orchestrator_id=orchestrator_id,
                agent_name="test-fifo-agent",
                role_filter="implement",
            )
            assert task is not None, "Expected a task but claim returned None"
            claimed_ids.append(task["id"])

        assert claimed_ids == task_ids, (
            f"Expected FIFO order {task_ids}, got {claimed_ids}"
        )
