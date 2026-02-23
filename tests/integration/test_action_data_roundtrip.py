"""Integration test: action_data round-trip.

Verifies that an action created with action_data (containing a buttons array)
is returned unchanged when fetched back from the server.
"""

import json


class TestActionDataRoundtrip:
    """Test that action_data with buttons array survives a create/fetch round-trip."""

    def test_action_data_roundtrip(self, scoped_sdk):
        """Create action with action_data containing buttons; verify round-trip."""
        buttons = [
            {"label": "Approve", "command": "/approve"},
            {"label": "Reject", "command": "/reject"},
        ]
        action_data = json.dumps({"buttons": buttons})

        # Use _request directly — sdk.actions.create() does not expose action_data.
        created = scoped_sdk._request(
            "POST",
            "/api/v1/actions",
            json={
                "entity_type": "task",
                "entity_id": "test-task-ad-001",
                "action_type": "review_task",
                "label": "Review this task",
                "proposed_by": "gatekeeper",
                "action_data": action_data,
            },
        )
        assert "id" in created
        action_id = created["id"]

        # Server returns action_data unchanged in the create response.
        assert created["action_data"] == action_data

        # Fetch back via list (no GET /api/v1/actions/:id endpoint exists).
        actions = scoped_sdk.actions.list(entity_id="test-task-ad-001")
        matched = [a for a in actions if a["id"] == action_id]
        assert len(matched) == 1, (
            f"Expected 1 action with id {action_id}, got {len(matched)}"
        )
        fetched = matched[0]

        assert fetched["action_data"] is not None

        # Parse and verify buttons structure.
        fetched_data = json.loads(fetched["action_data"])
        assert "buttons" in fetched_data

        fetched_buttons = fetched_data["buttons"]
        assert len(fetched_buttons) == 2

        assert fetched_buttons[0]["label"] == "Approve"
        assert fetched_buttons[0]["command"] == "/approve"
        assert fetched_buttons[1]["label"] == "Reject"
        assert fetched_buttons[1]["command"] == "/reject"

    def test_action_data_multiple_buttons(self, scoped_sdk):
        """Create action with multiple buttons; verify all labels and commands survive."""
        buttons = [
            {"label": "Approve", "command": "/approve"},
            {"label": "Reject", "command": "/reject reason"},
            {"label": "Requeue", "command": "/requeue"},
        ]
        action_data = json.dumps({"buttons": buttons})

        created = scoped_sdk._request(
            "POST",
            "/api/v1/actions",
            json={
                "entity_type": "draft",
                "entity_id": "test-draft-ad-001",
                "action_type": "review_draft",
                "label": "Review draft",
                "proposed_by": "gatekeeper",
                "action_data": action_data,
            },
        )
        action_id = created["id"]

        actions = scoped_sdk.actions.list(entity_id="test-draft-ad-001")
        fetched = next(a for a in actions if a["id"] == action_id)

        fetched_data = json.loads(fetched["action_data"])
        assert len(fetched_data["buttons"]) == 3

        labels = [b["label"] for b in fetched_data["buttons"]]
        commands = [b["command"] for b in fetched_data["buttons"]]
        assert labels == ["Approve", "Reject", "Requeue"]
        assert commands == ["/approve", "/reject reason", "/requeue"]

    def test_action_without_action_data(self, scoped_sdk):
        """Action created without action_data has None for that field on fetch."""
        created = scoped_sdk._request(
            "POST",
            "/api/v1/actions",
            json={
                "entity_type": "task",
                "entity_id": "test-task-ad-002",
                "action_type": "simple_action",
                "label": "A simple action",
                "proposed_by": "gatekeeper",
            },
        )
        action_id = created["id"]

        actions = scoped_sdk.actions.list(entity_id="test-task-ad-002")
        fetched = next(a for a in actions if a["id"] == action_id)

        assert fetched.get("action_data") is None
