"""Queue backpressure and status tracking.

This module handles queue limits, PR counting, and status monitoring
to prevent system overload. Also provides scheduler-specific backpressure
checks for different agent roles.
"""

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Tuple

from .config import get_queue_dir, get_queue_limits, get_orchestrator_dir


def count_queue(subdir: str) -> int:
    """Count tasks in a queue via API.

    Args:
        subdir: One of 'incoming', 'claimed', 'done', 'failed', 'provisional'

    Returns:
        Number of tasks
    """
    # Import here to avoid circular dependency
    from .tasks import list_tasks

    try:
        tasks = list_tasks(subdir)  # Already uses SDK
        return len(tasks)
    except Exception as e:
        print(f"Warning: Failed to count queue {subdir}: {e}")
        return 0


def _get_pr_cache_path() -> Path:
    """Get path to PR count cache file."""
    return get_queue_dir() / ".pr_cache.json"


def count_open_prs(author: str | None = None, cache_seconds: int = 60) -> int:
    """Count open pull requests via gh CLI with file-based caching.

    Args:
        author: Optional author to filter by (e.g., '@me' or username)
        cache_seconds: How long to cache the result (default 60s)

    Returns:
        Number of open PRs (0 if gh command fails)
    """
    cache_path = _get_pr_cache_path()

    # Check cache
    try:
        if cache_path.exists():
            cache_data = json.loads(cache_path.read_text())
            cached_time = datetime.fromisoformat(cache_data.get("timestamp", ""))
            if (datetime.now() - cached_time).total_seconds() < cache_seconds:
                return cache_data.get("count", 0)
    except (json.JSONDecodeError, ValueError, KeyError):
        pass  # Cache invalid, fetch fresh

    # Fetch from GitHub
    try:
        cmd = ["gh", "pr", "list", "--state", "open", "--json", "number"]
        if author:
            cmd.extend(["--author", author])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return 0

        prs = json.loads(result.stdout)
        count = len(prs)

        # Update cache
        cache_path.write_text(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "count": count,
        }))

        return count
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError):
        return 0


def can_create_task() -> tuple[bool, str]:
    """Check if a new task can be created (backpressure check).

    Returns:
        Tuple of (can_create, reason_if_not)
    """
    limits = get_queue_limits()

    incoming = count_queue("incoming")
    claimed = count_queue("claimed")
    total_pending = incoming + claimed

    if total_pending >= limits["max_incoming"]:
        return False, f"Queue full: {total_pending} pending tasks (limit: {limits['max_incoming']})"

    return True, ""


def can_claim_task() -> tuple[bool, str]:
    """Check if a task can be claimed (backpressure check).

    Returns:
        Tuple of (can_claim, reason_if_not)
    """
    limits = get_queue_limits()

    incoming = count_queue("incoming")
    if incoming == 0:
        return False, "No tasks in incoming queue"

    claimed = count_queue("claimed")
    if claimed >= limits["max_claimed"]:
        return False, f"Too many claimed tasks: {claimed} (limit: {limits['max_claimed']})"

    open_prs = count_open_prs()
    if open_prs >= limits["max_open_prs"]:
        return False, f"Too many open PRs: {open_prs} (limit: {limits['max_open_prs']})"

    return True, ""


def get_queue_status() -> dict[str, Any]:
    """Get overall queue status for monitoring.

    Returns:
        Dictionary with queue counts and task lists
    """
    # Import here to avoid circular dependency
    from .tasks import list_tasks
    from .projects import list_projects

    queues = ["incoming", "claimed", "needs_continuation", "done", "failed", "rejected",
              "breakdown", "provisional", "escalated"]

    result = {}
    for q in queues:
        tasks = list_tasks(q)
        result[q] = {
            "count": len(tasks),
            "tasks": tasks[-10:] if q in ("done", "rejected") else tasks,
        }

    result["limits"] = get_queue_limits()
    result["open_prs"] = count_open_prs()

    result["projects"] = {
        "draft": len(list_projects("draft")),
        "active": len(list_projects("active")),
        "ready-for-pr": len(list_projects("ready-for-pr")),
        "complete": len(list_projects("complete")),
    }

    return result


# =============================================================================
# Scheduler-specific backpressure checks
# =============================================================================


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
        max_claimed = limits.get("max_claimed", 1)

    count = count_queue("claimed")

    if count >= max_claimed:
        return False, f"claimed_limit:{count}/{max_claimed}"
    return True, ""


def check_incoming_tasks() -> Tuple[bool, str]:
    """Check if there are any incoming tasks to work on via API.

    Returns:
        Tuple of (can_proceed, reason)
    """
    count = count_queue("incoming")

    if count == 0:
        return False, "no_tasks"
    return True, ""


def check_breakdown_queue() -> Tuple[bool, str]:
    """Check if there are any tasks in the breakdown queue via API.

    Returns:
        Tuple of (can_proceed, reason)
    """
    count = count_queue("breakdown")

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
    count = count_queue("provisional")

    if count == 0:
        return False, "no_provisional_tasks"
    return True, ""


def check_gatekeeper_backpressure() -> Tuple[bool, str]:
    """Backpressure check for gatekeeper agents.

    Only proceed if there are provisional tasks with pending checks.

    Returns:
        Tuple of (can_proceed, reason)
    """
    # Import here to avoid circular dependency
    from .tasks import list_tasks

    try:
        tasks = list_tasks("provisional")
        for task in tasks:
            checks = task.get("checks", [])
            if not checks:
                continue
            if task.get("commits_count", 0) == 0:
                continue
            check_results = task.get("check_results", {})
            for check_name in checks:
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
