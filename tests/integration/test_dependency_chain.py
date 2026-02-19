"""Integration tests for task dependency chains (blocked_by).

Tests that:
1. Blocked tasks can't be claimed while their blocker is incomplete
2. Completing a blocker unblocks downstream tasks
3. Chain of dependencies resolves correctly
"""

import pytest
from tests.integration.flow_helpers import create_task, make_task_id


class TestDependencyChainClaim:
    """Verify that blocked_by prevents claiming and unblocks on completion."""

    def test_blocked_task_cannot_be_claimed(self, scoped_sdk, orchestrator_id):
        """A task with blocked_by set should not be claimable."""
        # Create blocker task
        blocker = create_task(scoped_sdk, role="implement")
        blocker_id = blocker["id"]

        # Create blocked task
        blocked = create_task(
            scoped_sdk, role="implement", blocked_by=blocker_id
        )
        blocked_id = blocked["id"]

        # Both are in incoming
        assert scoped_sdk.tasks.get(blocker_id)["queue"] == "incoming"
        assert scoped_sdk.tasks.get(blocked_id)["queue"] == "incoming"

        # Claim should return the blocker (unblocked), not the blocked task
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed is not None
        assert claimed["id"] == blocker_id

        # Try to claim again — blocked task should NOT be claimable
        second_claim = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent-2",
            role_filter="implement",
        )
        assert second_claim is None, (
            "Blocked task should not be claimable while blocker is incomplete"
        )

    def test_completing_blocker_unblocks_downstream(
        self, scoped_sdk, orchestrator_id
    ):
        """When a blocker moves to done, the blocked task becomes claimable."""
        # Create chain: A blocks B
        task_a = create_task(scoped_sdk, role="implement")
        task_a_id = task_a["id"]

        task_b = create_task(
            scoped_sdk, role="implement", blocked_by=task_a_id
        )
        task_b_id = task_b["id"]

        # Claim and complete A: incoming → claimed → submitted → done
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="impl-1",
            role_filter="implement",
        )
        scoped_sdk.tasks.submit(task_a_id, commits_count=1, turns_used=5)
        scoped_sdk.tasks.accept(task_a_id, accepted_by="gatekeeper")

        # Verify A is done
        assert scoped_sdk.tasks.get(task_a_id)["queue"] == "done"

        # B should now be claimable (blocked_by cleared by server)
        task_b_data = scoped_sdk.tasks.get(task_b_id)
        assert task_b_data["blocked_by"] is None or task_b_data["blocked_by"] == "", (
            f"blocked_by should be cleared after blocker completes, "
            f"got: {task_b_data['blocked_by']}"
        )

        claimed_b = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="impl-2",
            role_filter="implement",
        )
        assert claimed_b is not None
        assert claimed_b["id"] == task_b_id

    def test_three_task_chain(self, scoped_sdk, orchestrator_id):
        """A → B → C chain: completing A unblocks B, completing B unblocks C."""
        task_a = create_task(scoped_sdk, role="implement")
        task_a_id = task_a["id"]

        task_b = create_task(
            scoped_sdk, role="implement", blocked_by=task_a_id
        )
        task_b_id = task_b["id"]

        task_c = create_task(
            scoped_sdk, role="implement", blocked_by=task_b_id
        )
        task_c_id = task_c["id"]

        # Only A should be claimable
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="impl-1",
            role_filter="implement",
        )
        assert claimed["id"] == task_a_id

        # B and C should not be claimable
        assert scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="impl-2",
            role_filter="implement",
        ) is None

        # Complete A
        scoped_sdk.tasks.submit(task_a_id, commits_count=1, turns_used=3)
        scoped_sdk.tasks.accept(task_a_id, accepted_by="gatekeeper")

        # Now B should be claimable, but not C
        claimed_b = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="impl-1",
            role_filter="implement",
        )
        assert claimed_b is not None
        assert claimed_b["id"] == task_b_id

        assert scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="impl-2",
            role_filter="implement",
        ) is None

        # Complete B
        scoped_sdk.tasks.submit(task_b_id, commits_count=2, turns_used=4)
        scoped_sdk.tasks.accept(task_b_id, accepted_by="gatekeeper")

        # Now C should be claimable
        claimed_c = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="impl-1",
            role_filter="implement",
        )
        assert claimed_c is not None
        assert claimed_c["id"] == task_c_id

    def test_stuck_blocker_prevents_chain_progress(
        self, scoped_sdk, orchestrator_id
    ):
        """If blocker is stuck in claimed (orphaned), downstream stays blocked."""
        task_a = create_task(scoped_sdk, role="implement")
        task_a_id = task_a["id"]

        task_b = create_task(
            scoped_sdk, role="implement", blocked_by=task_a_id
        )

        # Claim A but don't complete it (simulates orphaned agent)
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="dead-agent",
            role_filter="implement",
        )

        # B should not be claimable while A is stuck in claimed
        claimed_b = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="impl-2",
            role_filter="implement",
        )
        assert claimed_b is None
