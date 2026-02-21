"""Action handler registry for the process_actions scheduler job.

Handlers are pure functions registered with @register_action.  The scheduler
job (process_actions in jobs.py) polls the server for execute_requested
actions, looks up the matching handler here, calls it, then marks the action
completed or failed via the SDK.

Adding a new action type:

    from .actions import register_action

    @register_action("my_action_type")
    def handle_my_action(action: dict) -> object:
        # action contains at minimum: id, action_type, payload
        result = do_something(action.get("payload", {}))
        return result  # returned value is stored as action.result on the server
"""

from __future__ import annotations

from typing import Any, Callable

# Registry mapping action_type → handler callable.
# Handler signature: (action: dict) -> Any
# Raise an exception to signal failure; return a value to signal success.
ACTION_REGISTRY: dict[str, Callable[[dict], Any]] = {}


def register_action(action_type: str) -> Callable:
    """Decorator — register a handler for the given action_type.

    Usage:
        @register_action("send_notification")
        def handle_send_notification(action: dict) -> dict:
            payload = action.get("payload", {})
            ...
            return {"sent": True}
    """
    def decorator(func: Callable[[dict], Any]) -> Callable[[dict], Any]:
        ACTION_REGISTRY[action_type] = func
        return func
    return decorator
