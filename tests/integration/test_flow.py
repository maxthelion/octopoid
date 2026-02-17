"""Integration tests for task lifecycle flow using scoped SDK.

These tests exercise the task state machine against the real local server,
with full isolation via scope — each test sees only its own data.

Run with a local server on port 9787:
    cd submodules/server && npx wrangler dev --port 9787
"""

import uuid

import pytest

from octopoid_sdk import OctopoidSDK
from tests.integration.flow_helpers import (
    create_and_claim,
    create_provisional,
    create_task,
    make_task_id,
)


class TestTaskLifecycleFlow:
    """Test task state machine transitions against real server."""

    def test_full_happy_path(self, scoped_sdk, orchestrator_id):
        """incoming → claimed → provisional → done"""
        task_id = f"TEST-{uuid.uuid4().hex[:8]}"
        task = scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Test task",
            role="implement",
            branch="main",
        )
        assert task["queue"] == "incoming"

        # Claim
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed is not None
        assert claimed["id"] == task_id

        # Submit
        submitted = scoped_sdk.tasks.submit(task_id, commits_count=1, turns_used=5)
        assert submitted["queue"] == "provisional"

        # Accept
        accepted = scoped_sdk.tasks.accept(task_id, accepted_by="test-gatekeeper")
        assert accepted["queue"] == "done"

    def test_reject_returns_to_incoming(self, scoped_sdk, orchestrator_id):
        """provisional → incoming on reject"""
        task_id = f"TEST-{uuid.uuid4().hex[:8]}"
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Reject test",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        scoped_sdk.tasks.submit(task_id, commits_count=1, turns_used=3)

        rejected = scoped_sdk.tasks.reject(task_id, reason="Tests fail")
        assert rejected["queue"] == "incoming"

    def test_scope_isolation(self, scoped_sdk, test_server_url):
        """Scoped SDK only sees its own tasks."""
        other_sdk = OctopoidSDK(
            server_url=test_server_url,
            scope=f"other-{uuid.uuid4().hex[:8]}",
        )
        try:
            task_id = f"TEST-{uuid.uuid4().hex[:8]}"
            scoped_sdk.tasks.create(
                id=task_id,
                file_path=f".octopoid/tasks/{task_id}.md",
                title="Scoped task",
                role="implement",
                branch="main",
            )

            # Same scope sees it
            mine = scoped_sdk.tasks.list(queue="incoming")
            assert any(t["id"] == task_id for t in mine)

            # Different scope does not
            theirs = other_sdk.tasks.list(queue="incoming")
            assert not any(t["id"] == task_id for t in theirs)
        finally:
            other_sdk.close()

    def test_claim_with_role_filter(self, scoped_sdk, orchestrator_id):
        """Claim only returns tasks matching the role filter."""
        # Create two tasks with different roles
        implement_task = create_task(scoped_sdk, role="implement")
        review_task = create_task(scoped_sdk, role="review")

        # Claiming with role_filter="review" should return the review task
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="review",
        )
        assert claimed is not None
        assert claimed["id"] == review_task["id"]
        assert claimed["role"] == "review"

        # Claiming again with role_filter="implement" should return the implement task
        claimed2 = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed2 is not None
        assert claimed2["id"] == implement_task["id"]
        assert claimed2["role"] == "implement"

    def test_claim_with_type_filter(self, scoped_sdk, orchestrator_id):
        """Claim only returns tasks matching the type filter."""
        product_task = create_task(scoped_sdk, role="implement", task_type="product")
        infra_task = create_task(scoped_sdk, role="implement", task_type="infra")

        # Claiming with type_filter="infra" should only return the infra task
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
            type_filter="infra",
        )
        assert claimed is not None
        assert claimed["id"] == infra_task["id"]

        # Claiming with type_filter="product" should return the product task
        claimed2 = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
            type_filter="product",
        )
        assert claimed2 is not None
        assert claimed2["id"] == product_task["id"]

    def test_requeue_returns_to_incoming(self, scoped_sdk, orchestrator_id):
        """claimed → incoming via requeue."""
        task_id, claimed = create_and_claim(scoped_sdk, orchestrator_id)
        assert claimed is not None
        assert claimed["queue"] == "claimed"

        requeued = scoped_sdk.tasks.requeue(task_id)
        assert requeued["queue"] == "incoming"

        # Should be claimable again
        reclaimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert reclaimed is not None
        assert reclaimed["id"] == task_id

    def test_double_claim_fails(self, scoped_sdk, orchestrator_id):
        """Can't claim an already-claimed task — second claim returns None or raises."""
        create_task(scoped_sdk, role="implement")

        # First claim succeeds
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed is not None

        # Second claim returns None (no more tasks in scope)
        second = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent-2",
            role_filter="implement",
        )
        assert second is None

    def test_submit_without_claim_fails(self, scoped_sdk):
        """Submitting an unclaimed task returns an error."""
        task = create_task(scoped_sdk, role="implement")
        task_id = task["id"]

        # Task is still in incoming — submit should fail
        with pytest.raises(Exception):
            scoped_sdk.tasks.submit(task_id, commits_count=1, turns_used=5)

    def test_reject_preserves_metadata(self, scoped_sdk, orchestrator_id):
        """Rejected task retains pr_number, commits_count etc."""
        task_id, _ = create_and_claim(scoped_sdk, orchestrator_id)

        # Submit with metadata
        scoped_sdk.tasks.submit(
            task_id,
            commits_count=3,
            turns_used=12,
            check_results="all passed",
        )

        # Reject and verify metadata is preserved
        rejected = scoped_sdk.tasks.reject(task_id, reason="Needs more tests")
        assert rejected["queue"] == "incoming"

        # Fetch the task and check metadata fields are preserved
        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["commits_count"] == 3
        assert task["turns_used"] == 12

    def test_blocked_task_not_claimable(self, scoped_sdk, orchestrator_id):
        """Task with blocked_by cannot be claimed until blocker is done."""
        # Create blocker task
        blocker = create_task(scoped_sdk, role="implement")
        blocker_id = blocker["id"]

        # Create dependent task blocked by blocker
        blocked_task = create_task(scoped_sdk, role="implement", blocked_by=blocker_id)
        blocked_id = blocked_task["id"]

        # Attempt to claim — should only get the blocker (not the blocked task)
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed is not None
        assert claimed["id"] == blocker_id
        assert claimed["id"] != blocked_id

        # No more claimable tasks (blocked task is still blocked)
        no_task = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent-2",
            role_filter="implement",
        )
        assert no_task is None

    def test_unblock_on_accept(self, scoped_sdk, orchestrator_id):
        """Accepting blocker task unblocks dependent."""
        # Create blocker and advance to done
        blocker_id = create_provisional(scoped_sdk, orchestrator_id)
        scoped_sdk.tasks.accept(blocker_id, accepted_by="test-gatekeeper")

        # Create dependent task blocked by done blocker
        blocked_task = create_task(scoped_sdk, role="implement", blocked_by=blocker_id)
        blocked_id = blocked_task["id"]

        # Now the blocked task should be claimable
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed is not None
        assert claimed["id"] == blocked_id

    def test_scope_claim_isolation(self, scoped_sdk, test_server_url, orchestrator_id):
        """Claiming in scope A doesn't see scope B's tasks."""
        scope_b_sdk = OctopoidSDK(
            server_url=test_server_url,
            scope=f"scope-b-{uuid.uuid4().hex[:8]}",
        )
        try:
            # Create a task in scope A (scoped_sdk)
            create_task(scoped_sdk, role="implement")

            # Claim from scope B — should find nothing
            claimed_in_b = scope_b_sdk.tasks.claim(
                orchestrator_id=orchestrator_id,
                agent_name="test-agent",
                role_filter="implement",
            )
            assert claimed_in_b is None

            # Claim from scope A — should find the task
            claimed_in_a = scoped_sdk.tasks.claim(
                orchestrator_id=orchestrator_id,
                agent_name="test-agent",
                role_filter="implement",
            )
            assert claimed_in_a is not None
        finally:
            scope_b_sdk.close()

    def test_pool_model_claim_uses_blueprint_name(self, scoped_sdk, orchestrator_id):
        """Tasks claimed by pool model use blueprint name as claimed_by."""
        task = create_task(scoped_sdk, role="implement")
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="implementer",  # blueprint name, not instance name
            role_filter="implement",
        )
        assert claimed is not None
        assert claimed["claimed_by"] == "implementer"
