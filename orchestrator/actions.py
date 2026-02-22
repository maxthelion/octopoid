"""Action handler registry for programmatic action execution.

Handlers are pure functions: they receive an action dict and SDK, perform
operations via SDK calls, and return a result dict. No side effects beyond
the SDK calls are allowed.

Usage:
    from orchestrator.actions import get_handler, register_action_handler

    @register_action_handler("my_action")
    def handle_my_action(action: dict, sdk) -> dict:
        ...
        return {"ok": True}
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Maps action_type string → handler callable
_HANDLER_REGISTRY: Dict[str, Callable[[Dict[str, Any], Any], Dict[str, Any]]] = {}


def register_action_handler(
    action_type: str,
) -> Callable[[Callable[[Dict[str, Any], Any], Dict[str, Any]]], Callable[[Dict[str, Any], Any], Dict[str, Any]]]:
    """Decorator to register a function as an action handler.

    Args:
        action_type: The action_type string this handler serves.

    Returns:
        Decorator that registers the function and returns it unchanged.

    Example:
        @register_action_handler("archive_draft")
        def handle(action: dict, sdk) -> dict:
            ...
            return {"ok": True}
    """
    def decorator(
        fn: Callable[[Dict[str, Any], Any], Dict[str, Any]],
    ) -> Callable[[Dict[str, Any], Any], Dict[str, Any]]:
        _HANDLER_REGISTRY[action_type] = fn
        return fn

    return decorator


def get_handler(
    action_type: str,
) -> Optional[Callable[[Dict[str, Any], Any], Dict[str, Any]]]:
    """Look up a handler by action_type.

    Args:
        action_type: The action_type to look up.

    Returns:
        Handler callable, or None if no handler is registered.
    """
    return _HANDLER_REGISTRY.get(action_type)


# ---------------------------------------------------------------------------
# Built-in handlers
# ---------------------------------------------------------------------------


@register_action_handler("archive_draft")
def _handle_archive_draft(action: Dict[str, Any], sdk: Any) -> Dict[str, Any]:
    """Set a draft's status to 'superseded'.

    Expects:
        action['entity_id']: Draft ID (string or int)

    Returns:
        Result dict with 'draft_id' and 'status'.
    """
    draft_id = action["entity_id"]
    sdk._request("PATCH", f"/api/v1/drafts/{draft_id}", json={"status": "superseded"})
    return {"draft_id": draft_id, "status": "superseded"}


@register_action_handler("update_draft_status")
def _handle_update_draft_status(action: Dict[str, Any], sdk: Any) -> Dict[str, Any]:
    """Update a draft's status to an arbitrary value from the payload.

    Expects:
        action['entity_id']: Draft ID
        action['payload']['status']: Target status string

    Returns:
        Result dict with 'draft_id' and 'status'.
    """
    draft_id = action["entity_id"]
    payload = action.get("payload") or {}
    new_status = payload["status"]
    sdk._request("PATCH", f"/api/v1/drafts/{draft_id}", json={"status": new_status})
    return {"draft_id": draft_id, "status": new_status}


@register_action_handler("requeue_task")
def _handle_requeue_task(action: Dict[str, Any], sdk: Any) -> Dict[str, Any]:
    """Move a task back to the incoming queue.

    Expects:
        action['entity_id']: Task ID

    Returns:
        Result dict with 'task_id' and 'queue'.
    """
    task_id = action["entity_id"]
    sdk.tasks.update(task_id, queue="incoming")
    return {"task_id": task_id, "queue": "incoming"}
