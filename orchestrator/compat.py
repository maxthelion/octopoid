"""Legacy/backwards-compatibility helpers.

These functions are deprecated and kept only for backwards compatibility
with existing code and tests. New code should use SDK-based operations instead.
"""

import re
from pathlib import Path
from typing import Any

from .config import get_queue_dir, get_tasks_file_dir


def get_queue_subdir(subdir: str) -> Path:
    """Get a specific queue subdirectory.

    DEPRECATED: Direct filesystem operations are deprecated. Use SDK instead.

    Args:
        subdir: One of 'incoming', 'claimed', 'done', 'failed'

    Returns:
        Path to the subdirectory
    """
    queue_dir = get_queue_dir()
    path = queue_dir / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


# All queue directories that find_task_file searches
ALL_QUEUE_DIRS = [
    "incoming", "claimed", "provisional", "done", "failed",
    "rejected", "escalated", "recycled", "breakdown",
    "needs_continuation",
]


def find_task_file(task_id: str) -> Path | None:
    """Find a task's markdown file in the tasks directory.

    DEPRECATED: Use SDK task operations instead.

    All task files live in .octopoid/tasks/. Also checks legacy queue
    subdirectories for backward compatibility with pre-migration files.

    Args:
        task_id: Task identifier (e.g. "9f5cda4b")

    Returns:
        Full Path to the task file, or None if not found
    """
    # Primary location: single tasks directory
    tasks_dir = get_tasks_file_dir()
    for pattern in [f"TASK-{task_id}.md", f"*{task_id}*.md"]:
        for candidate in tasks_dir.glob(pattern):
            if candidate.exists():
                return candidate

    # Legacy fallback: search queue subdirectories
    queue_dir = get_queue_dir()
    for subdir in ALL_QUEUE_DIRS:
        candidate = queue_dir / subdir / f"TASK-{task_id}.md"
        if candidate.exists():
            return candidate

    return None


def parse_task_file(task_path: Path) -> dict[str, Any] | None:
    """Parse a task file and extract metadata.

    DEPRECATED: Use SDK get_task() instead.

    Args:
        task_path: Path to the task .md file

    Returns:
        Dictionary with task metadata or None if invalid
    """
    try:
        content = task_path.read_text()
    except IOError:
        return None

    # Extract task ID from title
    title_match = re.search(r"^#\s*\[TASK-([^\]]+)\]\s*(.+)$", content, re.MULTILINE)
    task_id = title_match.group(1) if title_match else task_path.stem
    title = title_match.group(2).strip() if title_match else task_path.stem

    # Extract fields
    role_match = re.search(r"^ROLE:\s*(.+)$", content, re.MULTILINE)
    priority_match = re.search(r"^PRIORITY:\s*(.+)$", content, re.MULTILINE)
    branch_match = re.search(r"^BRANCH:\s*(.+)$", content, re.MULTILINE)
    created_match = re.search(r"^CREATED:\s*(.+)$", content, re.MULTILINE)
    created_by_match = re.search(r"^CREATED_BY:\s*(.+)$", content, re.MULTILINE)
    blocked_by_match = re.search(r"^BLOCKED_BY:\s*(.+)$", content, re.MULTILINE)
    checks_match = re.search(r"^CHECKS:\s*(.+)$", content, re.MULTILINE)
    breakdown_depth_match = re.search(r"^BREAKDOWN_DEPTH:\s*(\d+)$", content, re.MULTILINE)

    # Parse checks into a list
    checks: list[str] = []
    if checks_match:
        checks = [c.strip() for c in checks_match.group(1).strip().split(",") if c.strip()]

    # Task options
    skip_pr_match = re.search(r"^SKIP_PR:\s*(.+)$", content, re.MULTILINE)
    expedite_match = re.search(r"^EXPEDITE:\s*(.+)$", content, re.MULTILINE)

    # Continuation-related fields
    wip_branch_match = re.search(r"^WIP_BRANCH:\s*(.+)$", content, re.MULTILINE)
    last_agent_match = re.search(r"^LAST_AGENT:\s*(.+)$", content, re.MULTILINE)
    continuation_reason_match = re.search(r"^CONTINUATION_REASON:\s*(.+)$", content, re.MULTILINE)

    # Parse boolean fields (true/yes/1 are truthy)
    def parse_bool(match):
        if not match:
            return False
        val = match.group(1).strip().lower()
        return val in ("true", "yes", "1")

    return {
        "id": task_id,
        "title": title,
        "role": role_match.group(1) if role_match else "implement",
        "priority": priority_match.group(1) if priority_match else "P2",
        "branch": branch_match.group(1) if branch_match else None,
        "created": created_match.group(1) if created_match else None,
        "created_by": created_by_match.group(1) if created_by_match else "unknown",
        "blocked_by": blocked_by_match.group(1) if blocked_by_match else None,
        "checks": checks,
        "breakdown_depth": int(breakdown_depth_match.group(1)) if breakdown_depth_match else 0,
        "skip_pr": parse_bool(skip_pr_match),
        "expedite": parse_bool(expedite_match),
        "wip_branch": wip_branch_match.group(1) if wip_branch_match else None,
        "last_agent": last_agent_match.group(1) if last_agent_match else None,
        "continuation_reason": continuation_reason_match.group(1) if continuation_reason_match else None,
    }


def resolve_task_file(filename: str) -> Path | None:
    """Resolve a task filename to its full path.

    DEPRECATED: Use find_task_file() or SDK operations instead.

    Args:
        filename: Partial task filename or ID

    Returns:
        Full path to task file, or None if not found
    """
    # If it's already a TASK-xxx.md filename, find it
    if filename.startswith("TASK-") and filename.endswith(".md"):
        task_id = filename.replace("TASK-", "").replace(".md", "")
        return find_task_file(task_id)

    # Try extracting task ID if it's a path-like string
    match = re.search(r"TASK-([a-f0-9]+)", filename)
    if match:
        task_id = match.group(1)
        return find_task_file(task_id)

    # Try as a bare task ID
    return find_task_file(filename)
