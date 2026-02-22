"""Tests for ActionsAPI (SDK)."""

from unittest.mock import MagicMock

import pytest

from octopoid_sdk.client import ActionsAPI, OctopoidSDK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_sdk() -> OctopoidSDK:
    """Return an OctopoidSDK instance with a mocked HTTP session."""
    sdk = OctopoidSDK(server_url="http://test.local", api_key="testkey")
    sdk._request = MagicMock()
    return sdk


# ---------------------------------------------------------------------------
# ActionsAPI tests
# ---------------------------------------------------------------------------


class TestActionsAPICreate:
    def test_create_minimal(self):
        sdk = make_sdk()
        sdk._request.return_value = {"id": "act-1", "status": "pending"}

        result = sdk.actions.create(
            entity_type="task",
            entity_id="TASK-abc",
            action_type="requeue_task",
            label="Requeue this task",
        )

        sdk._request.assert_called_once_with(
            "POST",
            "/api/v1/actions",
            json={
                "entity_type": "task",
                "entity_id": "TASK-abc",
                "action_type": "requeue_task",
                "label": "Requeue this task",
            },
        )
        assert result["id"] == "act-1"

    def test_create_with_all_optional_fields(self):
        sdk = make_sdk()
        sdk._request.return_value = {"id": "act-2"}

        sdk.actions.create(
            entity_type="draft",
            entity_id="42",
            label="Archive",
            description="Archive this draft",
            action_data={"buttons": [{"label": "Archive", "command": "archive"}]},
            proposed_by="gatekeeper",
            expires_at="2026-12-31T00:00:00Z",
        )

        call_json = sdk._request.call_args[1]["json"]
        assert call_json["description"] == "Archive this draft"
        assert call_json["action_data"] == {"buttons": [{"label": "Archive", "command": "archive"}]}
        assert call_json["proposed_by"] == "gatekeeper"
        assert call_json["expires_at"] == "2026-12-31T00:00:00Z"

    def test_create_omits_none_optional_fields(self):
        sdk = make_sdk()
        sdk._request.return_value = {}

        sdk.actions.create(
            entity_type="task",
            entity_id="T-1",
            label="Requeue",
        )

        call_json = sdk._request.call_args[1]["json"]
        assert "action_data" not in call_json
        assert "description" not in call_json
        assert "proposed_by" not in call_json
        assert "expires_at" not in call_json

    def test_create_action_type_defaults_to_proposal(self):
        sdk = make_sdk()
        sdk._request.return_value = {"id": "act-3"}

        sdk.actions.create(
            entity_type="draft",
            entity_id="5",
            label="Review",
        )

        call_json = sdk._request.call_args[1]["json"]
        assert call_json["action_type"] == "proposal"


class TestActionsAPIList:
    def test_list_no_filters(self):
        sdk = make_sdk()
        sdk._request.return_value = {"actions": [{"id": "a1"}, {"id": "a2"}]}

        result = sdk.actions.list()

        sdk._request.assert_called_once_with("GET", "/api/v1/actions", params={})
        assert len(result) == 2

    def test_list_with_filters(self):
        sdk = make_sdk()
        sdk._request.return_value = {"actions": []}

        sdk.actions.list(entity_type="draft", entity_id="7", status="pending")

        sdk._request.assert_called_once_with(
            "GET",
            "/api/v1/actions",
            params={"entity_type": "draft", "entity_id": "7", "status": "pending"},
        )

    def test_list_handles_list_response(self):
        sdk = make_sdk()
        sdk._request.return_value = [{"id": "x"}]

        result = sdk.actions.list()

        assert result == [{"id": "x"}]

    def test_list_handles_unexpected_response(self):
        sdk = make_sdk()
        sdk._request.return_value = "unexpected"

        result = sdk.actions.list()

        assert result == []


class TestActionsAPIExecute:
    def test_execute(self):
        sdk = make_sdk()
        sdk._request.return_value = {"id": "act-5", "status": "execute_requested"}

        result = sdk.actions.execute("act-5")

        sdk._request.assert_called_once_with(
            "POST", "/api/v1/actions/act-5/execute", json={}
        )
        assert result["status"] == "execute_requested"


class TestActionsAPIComplete:
    def test_complete_without_result(self):
        sdk = make_sdk()
        sdk._request.return_value = {"id": "act-6", "status": "completed"}

        sdk.actions.complete("act-6")

        sdk._request.assert_called_once_with(
            "POST", "/api/v1/actions/act-6/complete", json={}
        )

    def test_complete_with_result(self):
        sdk = make_sdk()
        sdk._request.return_value = {}

        sdk.actions.complete("act-6", result={"ok": True})

        call_json = sdk._request.call_args[1]["json"]
        assert call_json == {"result": {"ok": True}}


class TestActionsAPIFail:
    def test_fail_without_result(self):
        sdk = make_sdk()
        sdk._request.return_value = {"id": "act-7", "status": "failed"}

        sdk.actions.fail("act-7")

        sdk._request.assert_called_once_with(
            "POST", "/api/v1/actions/act-7/fail", json={}
        )

    def test_fail_with_result(self):
        sdk = make_sdk()
        sdk._request.return_value = {}

        sdk.actions.fail("act-7", result={"error": "timeout"})

        call_json = sdk._request.call_args[1]["json"]
        assert call_json == {"result": {"error": "timeout"}}


class TestSDKHasActionsAttribute:
    def test_sdk_exposes_actions(self):
        sdk = make_sdk()
        assert hasattr(sdk, "actions")
        assert isinstance(sdk.actions, ActionsAPI)


