"""Integration tests for task lifecycle and state transitions."""

import pytest
from octopoid_sdk import OctopoidSDK


class TestBasicLifecycle:
    """Test complete task lifecycle flows."""

    def test_create_claim_submit_accept(self, sdk, orchestrator_id, clean_tasks):
        """Full task lifecycle: create → claim → submit → accept → done."""

        # 1. Create task
        task = sdk.tasks.create(
            id="lifecycle-001",
            file_path="/tmp/lifecycle-001.md",
            title="Lifecycle Test",
            role="implement",
            priority="P1"
        )
        assert task['queue'] == 'incoming'
        assert task['id'] == "lifecycle-001"

        # 2. Claim task
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement"
        )
        # This will FAIL with 500 error - exposing the bug!
        assert claimed is not None
        assert claimed['id'] == "lifecycle-001"
        assert claimed['queue'] == 'claimed'
        assert claimed['claimed_by'] == 'test-agent'
        assert 'claimed_at' in claimed

        # 3. Submit completion
        submitted = sdk.tasks.submit(
            task_id="lifecycle-001",
            commits_count=3,
            turns_used=5
        )
        assert submitted['queue'] == 'provisional'
        assert submitted['commits_count'] == 3
        assert submitted['turns_used'] == 5

        # 4. Accept
        accepted = sdk.tasks.accept(
            task_id="lifecycle-001",
            accepted_by="test-gatekeeper"
        )
        assert accepted['queue'] == 'done'
        assert accepted['accepted_by'] == 'test-gatekeeper'

    def test_claim_submit_reject_retry(self, sdk, orchestrator_id, clean_tasks):
        """Rejection flow: create → claim → submit → reject → incoming."""

        # Create and claim
        sdk.tasks.create(
            id="reject-001",
            file_path="/tmp/reject-001.md",
            title="Reject Test",
            role="implement"
        )

        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement"
        )
        assert claimed['id'] == "reject-001"

        # Submit
        submitted = sdk.tasks.submit(
            task_id="reject-001",
            commits_count=1,
            turns_used=2
        )
        assert submitted['queue'] == 'provisional'

        # Reject
        rejected = sdk.tasks.reject(
            task_id="reject-001",
            reason="Tests failed",
            rejected_by="test-gatekeeper"
        )
        assert rejected['queue'] == 'incoming'
        # Should track rejection count
        if 'rejection_count' in rejected:
            assert rejected['rejection_count'] >= 1

        # Can be claimed again
        reclaimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent-2",
            role_filter="implement"
        )
        assert reclaimed['id'] == "reject-001"
        assert reclaimed['claimed_by'] == "test-agent-2"

    def test_multiple_rejections(self, sdk, orchestrator_id, clean_tasks):
        """Task can be rejected multiple times."""
        # Create task
        sdk.tasks.create(
            id="multi-reject-001",
            file_path="/tmp/multi-reject-001.md",
            title="Multi Reject Test",
            role="implement"
        )

        for i in range(3):
            # Claim
            claimed = sdk.tasks.claim(
                orchestrator_id=orchestrator_id,
                agent_name=f"agent-{i}",
                role_filter="implement"
            )
            assert claimed['id'] == "multi-reject-001"

            # Submit
            sdk.tasks.submit(
                task_id="multi-reject-001",
                commits_count=1,
                turns_used=1
            )

            # Reject
            rejected = sdk.tasks.reject(
                task_id="multi-reject-001",
                reason=f"Rejection {i+1}",
                rejected_by="gatekeeper"
            )
            assert rejected['queue'] == 'incoming'


class TestClaimBehavior:
    """Test task claiming behavior and edge cases."""

    def test_claim_with_role_filter(self, sdk, orchestrator_id, clean_tasks):
        """Claim only returns tasks matching role filter."""
        # Create tasks with different roles
        sdk.tasks.create(
            id="test-implement",
            file_path="/tmp/test-implement.md",
            title="Implement Task",
            role="implement"
        )
        sdk.tasks.create(
            id="test-breakdown",
            file_path="/tmp/test-breakdown.md",
            title="Breakdown Task",
            role="breakdown"
        )

        # Claim with implement filter
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="implementer",
            role_filter="implement"
        )
        assert claimed is not None
        assert claimed['role'] == "implement"

    def test_claim_returns_none_when_no_tasks(self, sdk, orchestrator_id, clean_tasks):
        """Claim returns None when no tasks available."""
        # Don't create any tasks
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement"
        )
        # Should return None (404)
        assert claimed is None

    def test_claim_respects_priority(self, sdk, orchestrator_id, clean_tasks):
        """Claim returns highest priority task first."""
        # Create tasks with different priorities
        sdk.tasks.create(
            id="test-p2",
            file_path="/tmp/test-p2.md",
            title="P2 Task",
            role="implement",
            priority="P2"
        )
        sdk.tasks.create(
            id="test-p0",
            file_path="/tmp/test-p0.md",
            title="P0 Task",
            role="implement",
            priority="P0"
        )
        sdk.tasks.create(
            id="test-p1",
            file_path="/tmp/test-p1.md",
            title="P1 Task",
            role="implement",
            priority="P1"
        )

        # Claim should return P0 first
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement"
        )
        assert claimed is not None
        assert claimed['id'] == "test-p0"

    def test_claim_updates_claimed_by(self, sdk, orchestrator_id, clean_tasks):
        """Claim updates claimed_by and claimed_at fields."""
        sdk.tasks.create(
            id="claim-update-001",
            file_path="/tmp/claim-update-001.md",
            title="Claim Update Test",
            role="implement"
        )

        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="my-agent",
            role_filter="implement"
        )
        assert claimed['claimed_by'] == "my-agent"
        assert claimed['claimed_at'] is not None


class TestStateValidation:
    """Test state machine validation."""

    def test_cannot_submit_unclaimed_task(self, sdk, clean_tasks):
        """Cannot submit task that wasn't claimed."""
        sdk.tasks.create(
            id="unclaimed-001",
            file_path="/tmp/unclaimed-001.md",
            title="Unclaimed Task",
            role="implement"
        )

        # Try to submit without claiming
        with pytest.raises(Exception):  # Should raise error (409 or 400)
            sdk.tasks.submit(
                task_id="unclaimed-001",
                commits_count=1,
                turns_used=1
            )

    def test_cannot_accept_unclaimed_task(self, sdk, clean_tasks):
        """Cannot accept task that's not in provisional state."""
        sdk.tasks.create(
            id="not-provisional-001",
            file_path="/tmp/not-provisional-001.md",
            title="Not Provisional",
            role="implement"
        )

        # Try to accept task in incoming state
        with pytest.raises(Exception):
            sdk.tasks.accept(
                task_id="not-provisional-001",
                accepted_by="gatekeeper"
            )

    def test_cannot_claim_from_wrong_queue(self, sdk, orchestrator_id, clean_tasks):
        """Cannot claim task that's not in incoming queue."""
        # Create task directly in provisional queue
        sdk.tasks.create(
            id="wrong-queue-001",
            file_path="/tmp/wrong-queue-001.md",
            title="Wrong Queue",
            role="implement",
            queue="provisional"
        )

        # Claim should not return this task
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement"
        )
        # Should either return None or a different task
        if claimed:
            assert claimed['id'] != "wrong-queue-001"
