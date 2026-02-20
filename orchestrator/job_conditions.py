"""Condition functions for declarative scheduler jobs.

Conditions can be referenced in jobs.yaml to gate whether a job should run.
Each condition is registered with @register_condition and receives a context
dict containing poll_data and any other relevant state.

Usage in jobs.yaml:
  - name: some_job
    conditions:
      - no_agents_running
"""

from __future__ import annotations

from typing import Callable


# Registry mapping condition name → callable
CONDITION_REGISTRY: dict[str, Callable[[dict], bool]] = {}


def register_condition(func: Callable[[dict], bool]) -> Callable[[dict], bool]:
    """Decorator — register a condition function in CONDITION_REGISTRY."""
    CONDITION_REGISTRY[func.__name__] = func
    return func


@register_condition
def no_agents_running(ctx: dict) -> bool:
    """Return True if no agent instances are currently running.

    Checks all configured agent blueprints. Useful for jobs that should only
    run when the system is idle.
    """
    from .pool import count_running_instances
    from .config import get_agents

    try:
        agents = get_agents()
    except Exception:
        return True  # Fail open: assume idle when config unreadable

    for agent in agents:
        blueprint = agent.get("blueprint_name", agent.get("name", ""))
        if blueprint and count_running_instances(blueprint) > 0:
            return False

    return True


@register_condition
def has_open_prs(ctx: dict) -> bool:
    """Return True if the repository has at least one open pull request.

    Uses the gh CLI. Returns True on error (fail open) to avoid blocking jobs
    when the check itself fails.
    """
    import subprocess
    import json

    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--json", "number"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        prs = json.loads(result.stdout or "[]")
        return len(prs) > 0
    except Exception:
        return True  # Fail open
