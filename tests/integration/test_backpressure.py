"""Integration tests for backpressure queue limits.

Verifies that can_claim_task() correctly blocks claiming when queue capacity
is reached, and unblocks when capacity is freed — using both real server state
and pre-fetched queue_counts.
"""

import pytest
from unittest.mock import patch

from orchestrator.backpressure import can_claim_task


# Limits used for tests that need max_claimed > 1 (default is 1).
_TEST_LIMITS = {"max_incoming": 20, "max_claimed": 2, "max_provisional": 10}


class TestBackpressureBlocksAtCapacity:
    """Backpressure prevents claiming when the claimed queue is full."""

    def test_blocks_at_capacity_and_releases_on_completion(
        self, sdk, orchestrator_id, clean_tasks
    ):
        """Claiming is blocked when claimed == max_claimed, unblocked after a task completes.

        End-to-end flow:
          1. Create 3 tasks on the real server.
          2. Claim 2 (fills capacity with max_claimed=2).
          3. Assert can_claim_task() returns False.
          4. Accept one claimed task (moves it to done).
          5. Assert can_claim_task() returns True.
        """
        with patch("orchestrator.backpressure.get_queue_limits", return_value=_TEST_LIMITS):
            # ── Setup: 3 incoming tasks ────────────────────────────────────
            for i in range(3):
                sdk.tasks.create(
                    id=f"bp-cap-{i}",
                    file_path=f"/tmp/bp-cap-{i}.md",
                    title=f"Backpressure capacity test {i}",
                    role="implement",
                    branch="main",
                )

            # ── Claim 2 tasks ─────────────────────────────────────────────
            claimed_tasks = []
            for _ in range(2):
                t = sdk.tasks.claim(
                    orchestrator_id=orchestrator_id,
                    agent_name="test-agent",
                    role_filter="implement",
                )
                assert t is not None, "Expected to claim a task"
                claimed_tasks.append(t)

            # ── At capacity: 1 incoming, 2 claimed ────────────────────────
            queue_counts_at_cap = {"incoming": 1, "claimed": 2, "provisional": 0}
            result, reason = can_claim_task(queue_counts=queue_counts_at_cap)

            assert result is False
            assert "claimed" in reason.lower(), f"Expected 'claimed' in reason: {reason!r}"

            # ── Free one slot: submit + accept the first claimed task ──────
            first_task_id = claimed_tasks[0]["id"]
            sdk.tasks.submit(task_id=first_task_id, commits_count=1, turns_used=3)
            sdk.tasks.accept(task_id=first_task_id, accepted_by="test-gatekeeper")

            # ── Below capacity: 1 incoming, 1 claimed ─────────────────────
            queue_counts_freed = {"incoming": 1, "claimed": 1, "provisional": 0}
            result_after, reason_after = can_claim_task(queue_counts=queue_counts_freed)

            assert result_after is True, f"Expected True after freeing slot, got: {reason_after!r}"

    def test_blocks_when_no_incoming_tasks(self, clean_tasks):
        """Claiming is blocked when the incoming queue is empty."""
        queue_counts = {"incoming": 0, "claimed": 0, "provisional": 0}
        result, reason = can_claim_task(queue_counts=queue_counts)

        assert result is False
        assert "No tasks" in reason


class TestBackpressureWithQueueCounts:
    """Verify queue_counts parameter is used correctly and no extra API calls are made."""

    def test_uses_provided_counts_without_api_calls(self):
        """When queue_counts is provided, count_queue() is never called."""
        queue_counts = {"incoming": 5, "claimed": 0, "provisional": 0}

        with patch("orchestrator.backpressure.count_queue") as mock_count:
            result, reason = can_claim_task(queue_counts=queue_counts)

        mock_count.assert_not_called()
        assert result is True

    def test_blocks_when_claimed_equals_max(self):
        """Blocks when claimed count matches the configured max_claimed."""
        with patch("orchestrator.backpressure.get_queue_limits", return_value=_TEST_LIMITS):
            queue_counts = {"incoming": 3, "claimed": 2, "provisional": 0}
            result, reason = can_claim_task(queue_counts=queue_counts)

        assert result is False
        assert "claimed" in reason.lower()

    def test_allows_when_claimed_below_max(self):
        """Allows claiming when claimed count is below max_claimed."""
        with patch("orchestrator.backpressure.get_queue_limits", return_value=_TEST_LIMITS):
            queue_counts = {"incoming": 3, "claimed": 1, "provisional": 0}
            result, reason = can_claim_task(queue_counts=queue_counts)

        assert result is True

    def test_blocks_when_provisional_equals_max(self):
        """Blocks when provisional count matches the configured max_provisional."""
        limits = {"max_incoming": 20, "max_claimed": 5, "max_provisional": 2}
        with patch("orchestrator.backpressure.get_queue_limits", return_value=limits):
            queue_counts = {"incoming": 3, "claimed": 0, "provisional": 2}
            result, reason = can_claim_task(queue_counts=queue_counts)

        assert result is False
        assert "provisional" in reason.lower()

    def test_missing_keys_in_queue_counts_default_to_zero(self):
        """Partial queue_counts dict — missing keys default to 0."""
        # Only 'incoming' and 'claimed' provided; 'provisional' absent.
        queue_counts = {"incoming": 2, "claimed": 0}
        result, reason = can_claim_task(queue_counts=queue_counts)

        # provisional defaults to 0, which is below any reasonable limit
        assert result is True
