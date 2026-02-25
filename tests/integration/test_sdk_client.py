"""Integration tests for SDK client.py TasksAPI.claim() contract.

Verifies that claim() returns None on 404 (empty queue) and 429 (max_claimed
limit), and returns a claimed task dict when a task is available.

These tests use scoped_sdk for isolation — each test gets its own scope.
"""

import socket
import uuid

import pytest
import requests


TEST_SERVER_URL = "http://localhost:9787"


def _register_orchestrator(server_url: str, orchestrator_id: str, max_claimed: int = 5) -> None:
    """Register an orchestrator on the test server.

    Returns nothing — the caller constructs the orchestrator_id directly
    (as cluster-machine_id) rather than reading it from the response, since
    the response field is 'orchestrator_id', not 'id'.
    """
    cluster, machine_id = orchestrator_id.split("-", 1)
    requests.post(
        f"{server_url}/api/v1/orchestrators/register",
        json={
            "cluster": cluster,
            "machine_id": machine_id,
            "repo_url": "https://github.com/test/octopoid.git",
            "hostname": socket.gethostname(),
            "version": "2.0.0-test",
            "max_claimed": max_claimed,
        },
    ).raise_for_status()


class TestClaimContract:
    """Tests for TasksAPI.claim() None-on-404/429 contract."""

    def test_claim_returns_none_on_empty_queue(self, scoped_sdk):
        """claim() returns None when the queue is empty (server returns 404)."""
        orch_id = f"test-{uuid.uuid4().hex[:8]}"

        result = scoped_sdk.tasks.claim(
            orchestrator_id=orch_id,
            agent_name="test-agent",
        )

        assert result is None, (
            f"Expected None on empty queue, got: {result!r}"
        )

    def test_claim_returns_task_when_available(self, scoped_sdk):
        """claim() returns a task dict with queue='claimed' when a task exists."""
        task_id = f"sdk-test-{uuid.uuid4().hex[:8]}"
        orch_id = f"test-{uuid.uuid4().hex[:8]}"

        # Create a task in the incoming queue
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f"/tmp/{task_id}.md",
            title="SDK claim contract test",
            role="implement",
            branch="main",
        )

        # Claim it
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orch_id,
            agent_name="test-agent",
        )

        assert claimed is not None, "Expected a task, got None"
        assert claimed["id"] == task_id
        assert claimed["queue"] == "claimed"

    def test_claim_returns_none_at_max_claimed_limit(self, scoped_sdk):
        """claim() returns None when max_claimed limit is reached (server returns 429)."""
        orch_id = f"test-{uuid.uuid4().hex[:8]}"
        task_ids = [f"sdk-mc-{uuid.uuid4().hex[:8]}" for _ in range(2)]

        # Register orchestrator with max_claimed=1
        _register_orchestrator(
            server_url=TEST_SERVER_URL,
            orchestrator_id=orch_id,
            max_claimed=1,
        )

        # Create two tasks
        for task_id in task_ids:
            scoped_sdk.tasks.create(
                id=task_id,
                file_path=f"/tmp/{task_id}.md",
                title=f"SDK max_claimed test {task_id}",
                role="implement",
                branch="main",
            )

        # First claim succeeds
        first = scoped_sdk.tasks.claim(
            orchestrator_id=orch_id,
            agent_name="test-agent",
            max_claimed=1,
        )
        assert first is not None, "Expected first claim to succeed"
        assert first["queue"] == "claimed"

        # Second claim returns None (429 — max_claimed limit reached)
        second = scoped_sdk.tasks.claim(
            orchestrator_id=orch_id,
            agent_name="test-agent",
            max_claimed=1,
        )
        assert second is None, (
            f"Expected None when max_claimed limit reached, got: {second!r}"
        )
