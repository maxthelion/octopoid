"""Backpressure checks for the orchestrator.

These checks run BEFORE spawning an agent to avoid wasting resources.
Each check returns (can_proceed: bool, reason: str).
"""

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple

from .config import get_orchestrator_dir, get_queue_limits, is_db_enabled


def check_open_prs(max_prs: int = None) -> Tuple[bool, str]:
    """Check if there are too many open PRs.

    Uses a cache to avoid hitting GitHub API too frequently.
    Cache expires after 5 minutes.

    Args:
        max_prs: Maximum allowed open PRs. If None, uses config default.

    Returns:
        Tuple of (can_proceed, reason)
    """
    if max_prs is None:
        limits = get_queue_limits()
        max_prs = limits.get("max_open_prs", 10)

    cache_file = get_orchestrator_dir() / "shared" / "queue" / ".pr_cache.json"
    cache_ttl = timedelta(minutes=5)

    # Try to use cache
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
            cache_time = datetime.fromisoformat(cache["timestamp"])
            if datetime.now() - cache_time < cache_ttl:
                count = cache["count"]
                if count >= max_prs:
                    return False, f"pr_limit:{count}/{max_prs}"
                return True, ""
        except (json.JSONDecodeError, KeyError, ValueError):
            pass  # Cache invalid, refresh

    # Fetch from GitHub
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "number"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            prs = json.loads(result.stdout)
            count = len(prs)

            # Update cache
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "count": count,
            }))

            if count >= max_prs:
                return False, f"pr_limit:{count}/{max_prs}"
            return True, ""
    except Exception as e:
        # On error, be permissive
        return True, ""

    return True, ""


def check_claimed_tasks(max_claimed: int = None) -> Tuple[bool, str]:
    """Check if there are too many claimed tasks.

    Args:
        max_claimed: Maximum allowed claimed tasks. If None, uses config default.

    Returns:
        Tuple of (can_proceed, reason)
    """
    if max_claimed is None:
        limits = get_queue_limits()
        max_claimed = limits.get("max_claimed", 5)

    if is_db_enabled():
        from . import db
        count = db.count_tasks(queue="claimed")
    else:
        queue_dir = get_orchestrator_dir() / "shared" / "queue" / "claimed"
        count = len(list(queue_dir.glob("TASK-*.md"))) if queue_dir.exists() else 0

    if count >= max_claimed:
        return False, f"claimed_limit:{count}/{max_claimed}"
    return True, ""


def check_incoming_tasks() -> Tuple[bool, str]:
    """Check if there are any incoming tasks to work on.

    Returns:
        Tuple of (can_proceed, reason)
    """
    if is_db_enabled():
        from . import db
        count = db.count_tasks(queue="incoming")
    else:
        queue_dir = get_orchestrator_dir() / "shared" / "queue" / "incoming"
        count = len(list(queue_dir.glob("TASK-*.md"))) if queue_dir.exists() else 0

    if count == 0:
        return False, "no_tasks"
    return True, ""


def check_breakdown_queue() -> Tuple[bool, str]:
    """Check if there are any tasks in the breakdown queue.

    Returns:
        Tuple of (can_proceed, reason)
    """
    if is_db_enabled():
        from . import db
        count = db.count_tasks(queue="breakdown")
    else:
        queue_dir = get_orchestrator_dir() / "shared" / "queue" / "breakdown"
        count = len(list(queue_dir.glob("TASK-*.md"))) if queue_dir.exists() else 0

    if count == 0:
        return False, "no_breakdown_tasks"
    return True, ""


def check_implementer_backpressure() -> Tuple[bool, str]:
    """Combined backpressure check for implementer agents.

    Checks:
    1. Are there incoming tasks?
    2. Are there too many claimed tasks?
    3. Are there too many open PRs?

    Returns:
        Tuple of (can_proceed, reason)
    """
    # Check incoming tasks first (cheap)
    can_proceed, reason = check_incoming_tasks()
    if not can_proceed:
        return False, reason

    # Check claimed tasks (cheap)
    can_proceed, reason = check_claimed_tasks()
    if not can_proceed:
        return False, reason

    # Check open PRs (uses cache, may hit API)
    can_proceed, reason = check_open_prs()
    if not can_proceed:
        return False, reason

    return True, ""


def check_breakdown_backpressure() -> Tuple[bool, str]:
    """Backpressure check for breakdown agents.

    Returns:
        Tuple of (can_proceed, reason)
    """
    return check_breakdown_queue()


def check_recycler_backpressure() -> Tuple[bool, str]:
    """Backpressure check for recycler agent.

    Only proceed if there are tasks in the provisional queue.

    Returns:
        Tuple of (can_proceed, reason)
    """
    if is_db_enabled():
        from . import db
        count = db.count_tasks(queue="provisional")
    else:
        queue_dir = get_orchestrator_dir() / "shared" / "queue" / "provisional"
        count = len(list(queue_dir.glob("TASK-*.md"))) if queue_dir.exists() else 0

    if count == 0:
        return False, "no_provisional_tasks"
    return True, ""


def check_check_runner_backpressure() -> Tuple[bool, str]:
    """Backpressure check for check_runner agent.

    Only proceed if there are provisional tasks with pending automated checks.

    Returns:
        Tuple of (can_proceed, reason)
    """
    if not is_db_enabled():
        return False, "check_runner_requires_db"

    from . import db

    tasks = db.list_tasks(queue="provisional")
    for task in tasks:
        checks = task.get("checks", [])
        if not checks:
            continue
        check_results = task.get("check_results", {})
        has_pending = any(
            c not in check_results or check_results[c].get("status") not in ("pass", "fail")
            for c in checks
        )
        if has_pending:
            return True, ""

    return False, "no_pending_checks"


def check_gatekeeper_backpressure() -> Tuple[bool, str]:
    """Backpressure check for gatekeeper agents.

    Only proceed if there are provisional tasks with pending non-mechanical checks.
    Uses the DB check system (task.checks + task.check_results).

    Returns:
        Tuple of (can_proceed, reason)
    """
    if not is_db_enabled():
        return False, "gatekeeper_requires_db"

    try:
        from . import db
        from .roles.check_runner import VALID_CHECK_TYPES as MECHANICAL_CHECK_TYPES

        tasks = db.list_tasks(queue="provisional")
        for task in tasks:
            checks = task.get("checks", [])
            if not checks:
                continue
            if task.get("commits_count", 0) == 0:
                continue
            check_results = task.get("check_results", {})
            # Look for pending checks that are NOT mechanical
            for check_name in checks:
                if check_name in MECHANICAL_CHECK_TYPES:
                    continue
                if check_name not in check_results or check_results[check_name].get("status") not in ("pass", "fail"):
                    return True, ""

        return False, "no_pending_gatekeeper_checks"
    except Exception:
        return False, "gatekeeper_check_error"


# Map role to backpressure check function
ROLE_CHECKS = {
    "implementer": check_implementer_backpressure,
    "breakdown": check_breakdown_backpressure,
    "recycler": check_recycler_backpressure,
    "orchestrator_impl": check_implementer_backpressure,  # Same checks as implementer
    "tester": check_implementer_backpressure,  # Same checks as implementer
    "reviewer": check_implementer_backpressure,  # Same checks as implementer
    "gatekeeper": check_gatekeeper_backpressure,
    "check_runner": check_check_runner_backpressure,
}


def check_backpressure_for_role(role: str) -> Tuple[bool, str]:
    """Get the appropriate backpressure check for a role.

    Args:
        role: Agent role (implementer, breakdown, etc.)

    Returns:
        Tuple of (can_proceed, reason)
    """
    check_fn = ROLE_CHECKS.get(role)
    if check_fn:
        return check_fn()
    # No check defined for this role, allow
    return True, ""


# CLI entry point for shell-based pre_check
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m orchestrator.backpressure <role>")
        print("Roles: implementer, breakdown, tester, reviewer")
        sys.exit(1)

    role = sys.argv[1]
    can_proceed, reason = check_backpressure_for_role(role)

    if can_proceed:
        sys.exit(0)
    else:
        print(reason)
        sys.exit(1)
