"""Queue backpressure and status tracking."""

from typing import Any

from .config import get_queue_limits

def count_queue(subdir: str) -> int:
    """Count tasks in a queue via API."""
    from .tasks import list_tasks
    try:
        tasks = list_tasks(subdir)
        return len(tasks)
    except Exception as e:
        print(f"Warning: Failed to count queue {subdir}: {e}")
        return 0

def can_create_task() -> tuple[bool, str]:
    """Check if a new task can be created (backpressure check)."""
    limits = get_queue_limits()
    incoming = count_queue("incoming")
    claimed = count_queue("claimed")
    total_pending = incoming + claimed
    if total_pending >= limits["max_incoming"]:
        return False, f"Queue full: {total_pending} pending tasks (limit: {limits['max_incoming']})"
    return True, ""

def can_claim_task() -> tuple[bool, str]:
    """Check if a task can be claimed (backpressure check)."""
    limits = get_queue_limits()
    incoming = count_queue("incoming")
    if incoming == 0:
        return False, "No tasks in incoming queue"
    claimed = count_queue("claimed")
    if claimed >= limits["max_claimed"]:
        return False, f"Too many claimed tasks: {claimed} (limit: {limits['max_claimed']})"
    provisional = count_queue("provisional")
    if provisional >= limits["max_provisional"]:
        return False, f"Too many provisional tasks: {provisional} (limit: {limits['max_provisional']})"
    return True, ""

def get_queue_status() -> dict[str, Any]:
    """Get overall queue status for monitoring."""
    from .tasks import list_tasks
    from .projects import list_projects
    queues = ["incoming", "claimed", "needs_continuation", "done", "failed", "rejected",
              "breakdown", "provisional", "escalated"]
    result = {}
    for q in queues:
        tasks = list_tasks(q)
        result[q] = {"count": len(tasks), "tasks": tasks[-10:] if q in ("done", "rejected") else tasks}
    result["limits"] = get_queue_limits()
    result["provisional"] = count_queue("provisional")
    result["projects"] = {
        "draft": len(list_projects("draft")),
        "active": len(list_projects("active")),
        "ready-for-pr": len(list_projects("ready-for-pr")),
        "complete": len(list_projects("complete")),
    }
    return result
