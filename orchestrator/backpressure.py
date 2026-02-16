"""Queue backpressure and status tracking."""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import get_queue_dir, get_queue_limits

def count_queue(subdir: str) -> int:
    """Count tasks in a queue via API."""
    from .tasks import list_tasks
    try:
        tasks = list_tasks(subdir)
        return len(tasks)
    except Exception as e:
        print(f"Warning: Failed to count queue {subdir}: {e}")
        return 0

def _get_pr_cache_path() -> Path:
    """Get path to PR count cache file."""
    return get_queue_dir() / ".pr_cache.json"

def count_open_prs(author: str | None = None, cache_seconds: int = 60) -> int:
    """Count open pull requests via gh CLI with file-based caching."""
    cache_path = _get_pr_cache_path()
    try:
        if cache_path.exists():
            cache_data = json.loads(cache_path.read_text())
            cached_time = datetime.fromisoformat(cache_data.get("timestamp", ""))
            if (datetime.now() - cached_time).total_seconds() < cache_seconds:
                return cache_data.get("count", 0)
    except (json.JSONDecodeError, ValueError, KeyError):
        pass
    try:
        cmd = ["gh", "pr", "list", "--state", "open", "--json", "number"]
        if author:
            cmd.extend(["--author", author])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return 0
        prs = json.loads(result.stdout)
        count = len(prs)
        cache_path.write_text(json.dumps({"timestamp": datetime.now().isoformat(), "count": count}))
        return count
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError):
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
    open_prs = count_open_prs()
    if open_prs >= limits["max_open_prs"]:
        return False, f"Too many open PRs: {open_prs} (limit: {limits['max_open_prs']})"
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
    result["open_prs"] = count_open_prs()
    result["projects"] = {
        "draft": len(list_projects("draft")),
        "active": len(list_projects("active")),
        "ready-for-pr": len(list_projects("ready-for-pr")),
        "complete": len(list_projects("complete")),
    }
    return result
