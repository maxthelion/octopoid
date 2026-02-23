"""Integration tests for SDK client.py claim() contract.

Verifies the critical claim() contract that the scheduler depends on:
  - Returns None on 404 (empty queue — normal steady state)
  - Returns a task dict with queue='claimed' when a task is available
  - Returns None on 429 (max_claimed limit reached)

Uses scoped_sdk fixture for complete per-test isolation.
"""

import socket
import uuid

import pytest
import requests


def _register_orchestrator(server_url: str, scope: str) -> str:
    """Register a test orchestrator scoped to this test and return its ID."""
    cluster = "sdk-test"
    machine_id = f"sdk-{uuid.uuid4().hex[:8]}"
    result = requests.post(
        f"{server_url}/api/v1/orchestrators/register",
        json={
            "cluster": cluster,
            "machine_id": machine_id,
            "repo_url": "https://github.com/test/octopoid.git",
            "hostname": socket.gethostname(),
            "version": "2.0.0-test",
            "scope": scope,
        },
    ).json()
    return result["id"]


class TestClaimContract:
    """SDK TasksAPI.claim() contract: return values for 404, 200, and 429."""

    def test_claim_returns_none_on_empty_queue(self, scoped_sdk, test_server_url):
        """claim() returns None when the queue is empty (server returns 404).

        This is the normal steady-state the scheduler runs in most of the time.
        A raised exception here would crash the scheduler loop.
        """
        orch_id = _register_orchestrator(test_server_url, scoped_sdk.scope)

        result = scoped_sdk.tasks.claim(
            orchestrator_id=orch_id,
            agent_name="test-agent",
        )

        assert result is None

    def test_claim_returns_task_when_available(self, scoped_sdk, test_server_url):
        """claim() returns the claimed task dict when a task is in the queue.

        The returned task must have queue='claimed', confirming the server
        moved it out of incoming.
        """
        orch_id = _register_orchestrator(test_server_url, scoped_sdk.scope)

        task_id = f"sdk-claim-{uuid.uuid4().hex[:8]}"
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f"tasks/{task_id}.md",
            title="SDK claim contract test task",
            role="implement",
            branch="main",
        )

        result = scoped_sdk.tasks.claim(
            orchestrator_id=orch_id,
            agent_name="test-agent",
        )

        assert result is not None, "claim() should return a task when one is available"
        assert result["id"] == task_id
        assert result["queue"] == "claimed"

    def test_claim_returns_none_at_max_claimed_limit(self, scoped_sdk, test_server_url):
        """claim() returns None when max_claimed limit is reached (server returns 429).

        With max_claimed=1:
          - First claim: 0 already claimed → server grants it → returns task
          - Second claim: 1 already claimed (== max_claimed) → server returns 429
            → claim() must return None, not raise
        """
        orch_id = _register_orchestrator(test_server_url, scoped_sdk.scope)

        # Create two tasks so the second claim attempt has something to find
        for i in range(2):
            task_id = f"sdk-maxclaim-{uuid.uuid4().hex[:8]}"
            scoped_sdk.tasks.create(
                id=task_id,
                file_path=f"tasks/{task_id}.md",
                title=f"SDK max_claimed test task {i}",
                role="implement",
                branch="main",
            )

        # First claim succeeds — orchestrator has 0 claimed tasks, limit is 1
        first = scoped_sdk.tasks.claim(
            orchestrator_id=orch_id,
            agent_name="test-agent",
            max_claimed=1,
        )
        assert first is not None, "First claim should succeed when below max_claimed"

        # Second claim returns None — orchestrator now has 1 claimed task == max_claimed
        second = scoped_sdk.tasks.claim(
            orchestrator_id=orch_id,
            agent_name="test-agent",
            max_claimed=1,
        )
        assert second is None, (
            "claim() must return None (not raise) when max_claimed limit is reached"
        )
