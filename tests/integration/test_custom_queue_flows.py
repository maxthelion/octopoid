"""Integration tests for custom queue flows using extensible queue validation.

These tests verify that:
1. Tasks can move through custom queues registered via sdk.flows.register()
2. Invalid queues (not registered in any flow) are rejected by the server

The server supports extensible queue validation: custom queues must be declared
in a flow definition via PUT /api/v1/flows/:name before tasks can use them.

Run with a local server on port 9787:
    cd submodules/server && npx wrangler dev --port 9787
"""

import uuid

import pytest
import requests

from tests.integration.flow_helpers import create_task, make_task_id


def _register_flow(sdk, name: str, states: list, transitions: list) -> None:
    """Register a flow, skipping the test if the server doesn't support the flows API."""
    try:
        sdk.flows.register(name=name, states=states, transitions=transitions)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            pytest.skip(
                f"Server does not support PUT /api/v1/flows/{name} (404) — "
                "deploy server with extensible queue validation to run these tests"
            )
        raise


class TestCustomQueueFlows:
    """Test task transitions through custom queues registered via flow definitions."""

    def test_task_moves_through_custom_queues(self, scoped_sdk, orchestrator_id):
        """Task transitions through custom queues: incoming → testing → staging → done.

        1. Register a flow with custom queues via sdk.flows.register()
        2. Create a task
        3. Move task through custom queues via sdk.tasks.update()
        4. Assert each transition succeeds and the queue is updated
        """
        # Register a flow that includes custom queues
        _register_flow(
            scoped_sdk,
            name="default",
            states=["incoming", "claimed", "testing", "staging", "done"],
            transitions=[
                {"from": "incoming", "to": "claimed"},
                {"from": "claimed", "to": "testing"},
                {"from": "testing", "to": "staging"},
                {"from": "staging", "to": "done"},
            ],
        )

        # Create a task (starts in incoming)
        task = create_task(scoped_sdk, role="implement")
        task_id = task["id"]
        assert task["queue"] == "incoming"

        # Claim the task (incoming → claimed)
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed is not None
        assert claimed["id"] == task_id
        assert claimed["queue"] == "claimed"

        # Move to custom queue: claimed → testing
        in_testing = scoped_sdk.tasks.update(task_id, queue="testing")
        assert in_testing["queue"] == "testing"

        # Move to next custom queue: testing → staging
        in_staging = scoped_sdk.tasks.update(task_id, queue="staging")
        assert in_staging["queue"] == "staging"

        # Complete the task: staging → done
        done = scoped_sdk.tasks.update(task_id, queue="done")
        assert done["queue"] == "done"

        # Verify final state via get
        final = scoped_sdk.tasks.get(task_id)
        assert final is not None
        assert final["queue"] == "done"

    def test_invalid_queue_rejected(self, scoped_sdk):
        """Moving a task to an unregistered queue returns a 400 error.

        1. Register a flow with a specific set of states
        2. Create a task
        3. Attempt to move the task to a queue not in the registered flow
        4. Assert: server returns 400 error
        """
        # Register a flow with a known, limited set of queues
        _register_flow(
            scoped_sdk,
            name="default",
            states=["incoming", "claimed", "testing", "staging", "done"],
            transitions=[
                {"from": "incoming", "to": "claimed"},
                {"from": "claimed", "to": "testing"},
                {"from": "testing", "to": "staging"},
                {"from": "staging", "to": "done"},
            ],
        )

        # Create a task
        task = create_task(scoped_sdk, role="implement")
        task_id = task["id"]
        assert task["queue"] == "incoming"

        # Attempt to move to a queue that was NOT registered in the flow
        with pytest.raises(requests.HTTPError) as exc_info:
            scoped_sdk.tasks.update(task_id, queue="completely_bogus_queue")

        assert exc_info.value.response.status_code == 400
