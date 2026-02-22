"""Tests for ActionsAPI (SDK) and orchestrator action handler registry."""

from unittest.mock import MagicMock, call, patch

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
            action_type="archive_draft",
            label="Archive",
            payload={"reason": "obsolete"},
            proposed_by="gatekeeper",
            expires_at="2026-12-31T00:00:00Z",
        )

        call_json = sdk._request.call_args[1]["json"]
        assert call_json["payload"] == {"reason": "obsolete"}
        assert call_json["proposed_by"] == "gatekeeper"
        assert call_json["expires_at"] == "2026-12-31T00:00:00Z"

    def test_create_omits_none_optional_fields(self):
        sdk = make_sdk()
        sdk._request.return_value = {}

        sdk.actions.create(
            entity_type="task",
            entity_id="T-1",
            action_type="requeue_task",
            label="Requeue",
        )

        call_json = sdk._request.call_args[1]["json"]
        assert "payload" not in call_json
        assert "proposed_by" not in call_json
        assert "expires_at" not in call_json


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


# ---------------------------------------------------------------------------
# Handler registry tests
# ---------------------------------------------------------------------------


class TestHandlerRegistry:
    def test_register_and_get_handler(self):
        from orchestrator.actions import get_handler, register_action_handler

        @register_action_handler("_test_custom_action")
        def my_handler(action: dict, sdk) -> dict:
            return {"done": True}

        handler = get_handler("_test_custom_action")
        assert handler is not None
        assert handler({"entity_id": "x"}, None) == {"done": True}

    def test_get_handler_returns_none_for_unknown(self):
        from orchestrator.actions import get_handler

        result = get_handler("nonexistent_action_xyz")
        assert result is None

    def test_decorator_returns_function_unchanged(self):
        from orchestrator.actions import register_action_handler

        @register_action_handler("_test_return_fn")
        def my_fn(action: dict, sdk) -> dict:
            return {}

        # The decorator should return the original function
        assert callable(my_fn)
        assert my_fn({}, None) == {}


# ---------------------------------------------------------------------------
# archive_draft handler
# ---------------------------------------------------------------------------


class TestArchiveDraftHandler:
    def test_archive_draft_calls_patch(self):
        from orchestrator.actions import get_handler

        sdk = MagicMock()
        action = {
            "entity_type": "draft",
            "entity_id": "42",
            "action_type": "archive_draft",
            "payload": None,
        }

        handler = get_handler("archive_draft")
        result = handler(action, sdk)

        sdk._request.assert_called_once_with(
            "PATCH", "/api/v1/drafts/42", json={"status": "superseded"}
        )
        assert result == {"draft_id": "42", "status": "superseded"}

    def test_archive_draft_result_dict(self):
        from orchestrator.actions import get_handler

        sdk = MagicMock()
        action = {"entity_id": "99"}

        handler = get_handler("archive_draft")
        result = handler(action, sdk)

        assert result["draft_id"] == "99"
        assert result["status"] == "superseded"


# ---------------------------------------------------------------------------
# update_draft_status handler
# ---------------------------------------------------------------------------


class TestUpdateDraftStatusHandler:
    def test_update_draft_status(self):
        from orchestrator.actions import get_handler

        sdk = MagicMock()
        action = {
            "entity_id": "7",
            "payload": {"status": "ready"},
        }

        handler = get_handler("update_draft_status")
        result = handler(action, sdk)

        sdk._request.assert_called_once_with(
            "PATCH", "/api/v1/drafts/7", json={"status": "ready"}
        )
        assert result == {"draft_id": "7", "status": "ready"}

    def test_update_draft_status_various_statuses(self):
        from orchestrator.actions import get_handler

        sdk = MagicMock()
        handler = get_handler("update_draft_status")

        for status in ("idea", "draft", "ready", "superseded"):
            sdk.reset_mock()
            action = {"entity_id": "1", "payload": {"status": status}}
            result = handler(action, sdk)
            assert result["status"] == status


# ---------------------------------------------------------------------------
# requeue_task handler
# ---------------------------------------------------------------------------


class TestRequeueTaskHandler:
    def test_requeue_task(self):
        from orchestrator.actions import get_handler

        sdk = MagicMock()
        action = {
            "entity_type": "task",
            "entity_id": "TASK-abc123",
            "action_type": "requeue_task",
        }

        handler = get_handler("requeue_task")
        result = handler(action, sdk)

        sdk.tasks.update.assert_called_once_with("TASK-abc123", queue="incoming")
        assert result == {"task_id": "TASK-abc123", "queue": "incoming"}

    def test_requeue_task_result_dict(self):
        from orchestrator.actions import get_handler

        sdk = MagicMock()
        handler = get_handler("requeue_task")
        result = handler({"entity_id": "T-xyz"}, sdk)

        assert result["task_id"] == "T-xyz"
        assert result["queue"] == "incoming"
