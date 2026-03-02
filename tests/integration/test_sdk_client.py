"""Integration tests for SDK client.py TasksAPI.claim() contract.

Verifies that claim() returns None on 404 (empty queue) and 429 (max_claimed
limit), and returns a claimed task dict when a task is available.

These tests use scoped_sdk for isolation — each test gets its own scope.
"""

import socket
import uuid
from typing import Optional

import pytest
import requests


TEST_SERVER_URL = "http://localhost:9787"


def _register_orchestrator(
    server_url: str,
    orchestrator_id: str,
    max_claimed: int = 5,
    scope: Optional[str] = None,
) -> None:
    """Register an orchestrator on the test server.

    Returns nothing — the caller constructs the orchestrator_id directly
    (as cluster-machine_id) rather than reading it from the response, since
    the response field is 'orchestrator_id', not 'id'.
    """
    cluster, machine_id = orchestrator_id.split("-", 1)
    payload: dict = {
        "cluster": cluster,
        "machine_id": machine_id,
        "repo_url": "https://github.com/test/octopoid.git",
        "hostname": socket.gethostname(),
        "version": "2.0.0-test",
        "max_claimed": max_claimed,
    }
    if scope is not None:
        payload["scope"] = scope
    requests.post(
        f"{server_url}/api/v1/orchestrators/register",
        json=payload,
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

        # Register the orchestrator (required — claim enforces FK constraint)
        _register_orchestrator(
            server_url=TEST_SERVER_URL,
            orchestrator_id=orch_id,
            scope=scoped_sdk.scope,
        )

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
        """claim() returns None when the server returns 429 (max_claimed limit).

        The server does not currently enforce max_claimed server-side — that
        guard lives in backpressure.can_claim_task(). This test verifies the
        SDK client contract: a 429 HTTP response from the server maps to
        None rather than raising an exception.
        """
        from unittest.mock import MagicMock, patch

        orch_id = f"test-{uuid.uuid4().hex[:8]}"

        # Simulate a 429 response from the server
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            response=mock_response
        )

        with patch.object(scoped_sdk.session, "request", return_value=mock_response):
            result = scoped_sdk.tasks.claim(
                orchestrator_id=orch_id,
                agent_name="test-agent",
                max_claimed=1,
            )

        assert result is None, (
            f"Expected None when server returns 429, got: {result!r}"
        )
