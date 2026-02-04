"""Queue management with atomic operations and backpressure.

IMPORTANT: Queue operations always happen in the MAIN REPO, not in agent worktrees.
This ensures queue state is centralized and not affected by git operations in worktrees.

The queue directory is determined by:
1. ORCHESTRATOR_DIR environment variable (set by scheduler for agents)
2. Fallback to find_parent_project() for scheduler itself
"""

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import get_queue_dir as _config_get_queue_dir, get_queue_limits
from .lock_utils import locked


def get_queue_dir() -> Path:
    """Get the shared queue directory in the MAIN REPO.

    Always returns the main repo's queue, not a worktree's.
    Uses ORCHESTRATOR_DIR env var if set (agents), otherwise falls back
    to config's find_parent_project (scheduler).
    """
    orchestrator_dir = os.environ.get("ORCHESTRATOR_DIR")
    if orchestrator_dir:
        return Path(orchestrator_dir) / "shared" / "queue"
    return _config_get_queue_dir()


def get_queue_subdir(subdir: str) -> Path:
    """Get a specific queue subdirectory.

    Args:
        subdir: One of 'incoming', 'claimed', 'done', 'failed'

    Returns:
        Path to the subdirectory
    """
    queue_dir = get_queue_dir()
    path = queue_dir / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


def count_queue(subdir: str) -> int:
    """Count .md files in a queue subdirectory.

    Args:
        subdir: One of 'incoming', 'claimed', 'done', 'failed'

    Returns:
        Number of .md files
    """
    path = get_queue_subdir(subdir)
    return len(list(path.glob("*.md")))


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
    import json

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


def list_tasks(subdir: str) -> list[dict[str, Any]]:
    """List tasks in a queue subdirectory with metadata.

    Args:
        subdir: One of 'incoming', 'claimed', 'done', 'failed'

    Returns:
        List of task dictionaries with path, id, role, priority, created, title
    """
    path = get_queue_subdir(subdir)
    tasks = []

    for task_file in path.glob("*.md"):
        task_info = parse_task_file(task_file)
        if task_info:
            tasks.append(task_info)

    # Sort by: 1) expedite flag (expedited first), 2) priority (P0 first), 3) created time
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    tasks.sort(key=lambda t: (
        0 if t.get("expedite") else 1,  # Expedited tasks first
        priority_order.get(t.get("priority", "P2"), 2),
        t.get("created") or "",
    ))

    return tasks


def parse_task_file(task_path: Path) -> dict[str, Any] | None:
    """Parse a task file and extract metadata.

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
        "path": task_path,
        "id": task_id,
        "title": title,
        "role": role_match.group(1).strip() if role_match else None,
        "priority": priority_match.group(1).strip() if priority_match else "P2",
        "branch": branch_match.group(1).strip() if branch_match else "main",
        "created": created_match.group(1).strip() if created_match else None,
        "created_by": created_by_match.group(1).strip() if created_by_match else None,
        "skip_pr": parse_bool(skip_pr_match),
        "expedite": parse_bool(expedite_match),
        "wip_branch": wip_branch_match.group(1).strip() if wip_branch_match else None,
        "last_agent": last_agent_match.group(1).strip() if last_agent_match else None,
        "continuation_reason": continuation_reason_match.group(1).strip() if continuation_reason_match else None,
        "content": content,
    }


def claim_task(role_filter: str | None = None, agent_name: str | None = None) -> dict[str, Any] | None:
    """Atomically claim a task from incoming queue.

    Uses file locking + os.rename for robust race condition prevention.

    Args:
        role_filter: Only claim tasks with this role (e.g., 'implement', 'test')
        agent_name: Name of claiming agent (for logging in task)

    Returns:
        Task info dictionary if claimed, None if no suitable task
    """
    incoming_dir = get_queue_subdir("incoming")
    claimed_dir = get_queue_subdir("claimed")

    # Use a global claim lock to prevent race conditions
    lock_file = get_queue_dir() / ".claim.lock"

    tasks = list_tasks("incoming")

    for task in tasks:
        # Filter by role if specified
        if role_filter and task.get("role") != role_filter:
            continue

        source = task["path"]
        dest = claimed_dir / source.name

        # Try to acquire lock (non-blocking)
        with locked(lock_file, blocking=False) as acquired:
            if not acquired:
                # Another agent is claiming, skip this task
                continue

            try:
                # Double-check file still exists (another agent might have claimed it)
                if not source.exists():
                    continue

                # Atomic rename - will fail if file was already claimed
                os.rename(source, dest)

                # Add claim metadata to file
                if agent_name:
                    with open(dest, "a") as f:
                        f.write(f"\nCLAIMED_BY: {agent_name}\n")
                        f.write(f"CLAIMED_AT: {datetime.now().isoformat()}\n")

                task["path"] = dest
                return task

            except FileNotFoundError:
                # Task was claimed by another agent, try next
                continue
            except OSError:
                # Other error, try next
                continue

    return None


def complete_task(task_path: Path | str, result: str | None = None) -> Path:
    """Move a task to the done queue.

    Args:
        task_path: Path to the claimed task file
        result: Optional result summary to append

    Returns:
        New path in done queue
    """
    task_path = Path(task_path)
    done_dir = get_queue_subdir("done")
    dest = done_dir / task_path.name

    # Append completion info
    with open(task_path, "a") as f:
        f.write(f"\nCOMPLETED_AT: {datetime.now().isoformat()}\n")
        if result:
            f.write(f"\n## Result\n{result}\n")

    os.rename(task_path, dest)
    return dest


def fail_task(task_path: Path | str, error: str) -> Path:
    """Move a task to the failed queue with error information.

    Args:
        task_path: Path to the claimed task file
        error: Error message/description

    Returns:
        New path in failed queue
    """
    task_path = Path(task_path)
    failed_dir = get_queue_subdir("failed")
    dest = failed_dir / task_path.name

    # Append error info
    with open(task_path, "a") as f:
        f.write(f"\nFAILED_AT: {datetime.now().isoformat()}\n")
        f.write(f"\n## Error\n```\n{error}\n```\n")

    os.rename(task_path, dest)
    return dest


def reject_task(
    task_path: Path | str,
    reason: str,
    details: str | None = None,
    rejected_by: str | None = None,
) -> Path:
    """Reject a task and move it to the rejected queue.

    Use this when a task cannot or should not be completed, for example:
    - Functionality already exists (already_implemented)
    - Task is blocked by unmet dependencies (blocked)
    - Task doesn't make sense or is invalid (invalid_task)
    - Task duplicates another task (duplicate)
    - Task is out of scope for the agent (out_of_scope)

    Args:
        task_path: Path to the claimed task file
        reason: Rejection reason code (already_implemented, blocked, invalid_task, duplicate, out_of_scope)
        details: Detailed explanation of why the task is being rejected
        rejected_by: Name of the agent rejecting the task

    Returns:
        New path in rejected queue
    """
    task_path = Path(task_path)
    rejected_dir = get_queue_subdir("rejected")
    dest = rejected_dir / task_path.name

    # Append rejection info
    with open(task_path, "a") as f:
        f.write(f"\nREJECTED_AT: {datetime.now().isoformat()}\n")
        f.write(f"REJECTION_REASON: {reason}\n")
        if rejected_by:
            f.write(f"REJECTED_BY: {rejected_by}\n")
        if details:
            f.write(f"\n## Rejection Details\n{details}\n")

    os.rename(task_path, dest)
    return dest


def retry_task(task_path: Path | str) -> Path:
    """Move a task from failed back to incoming queue.

    Args:
        task_path: Path to the failed task file

    Returns:
        New path in incoming queue
    """
    task_path = Path(task_path)
    incoming_dir = get_queue_subdir("incoming")
    dest = incoming_dir / task_path.name

    # Append retry info
    with open(task_path, "a") as f:
        f.write(f"\nRETRIED_AT: {datetime.now().isoformat()}\n")

    os.rename(task_path, dest)
    return dest


def mark_needs_continuation(
    task_path: Path | str,
    reason: str,
    branch_name: str | None = None,
    agent_name: str | None = None,
) -> Path:
    """Mark a task as needing continuation and move to needs_continuation queue.

    Use this when an agent exits before completing work (e.g., max turns reached).
    The task can be resumed by the same or another agent.

    Args:
        task_path: Path to the claimed task file
        reason: Why continuation is needed (e.g., "max_turns_reached", "uncommitted_changes")
        branch_name: Branch where work-in-progress exists
        agent_name: Agent that was working on the task

    Returns:
        New path in needs_continuation queue
    """
    task_path = Path(task_path)
    continuation_dir = get_queue_subdir("needs_continuation")
    dest = continuation_dir / task_path.name

    # Append continuation info
    with open(task_path, "a") as f:
        f.write(f"\nNEEDS_CONTINUATION_AT: {datetime.now().isoformat()}\n")
        f.write(f"CONTINUATION_REASON: {reason}\n")
        if branch_name:
            f.write(f"WIP_BRANCH: {branch_name}\n")
        if agent_name:
            f.write(f"LAST_AGENT: {agent_name}\n")

    os.rename(task_path, dest)
    return dest


def resume_task(task_path: Path | str, agent_name: str | None = None) -> Path:
    """Move a task from needs_continuation back to claimed for resumption.

    Args:
        task_path: Path to the needs_continuation task file
        agent_name: Agent resuming the task

    Returns:
        New path in claimed queue
    """
    task_path = Path(task_path)
    claimed_dir = get_queue_subdir("claimed")
    dest = claimed_dir / task_path.name

    # Append resume info
    with open(task_path, "a") as f:
        f.write(f"\nRESUMED_AT: {datetime.now().isoformat()}\n")
        if agent_name:
            f.write(f"RESUMED_BY: {agent_name}\n")

    os.rename(task_path, dest)
    return dest


def find_task_by_id(task_id: str, subdirs: list[str] | None = None) -> dict[str, Any] | None:
    """Find a task by its ID across queue subdirectories.

    Args:
        task_id: Task ID to find (e.g., "9f5cda4b")
        subdirs: List of subdirs to search (default: all)

    Returns:
        Task info dict or None if not found
    """
    if subdirs is None:
        subdirs = ["incoming", "claimed", "needs_continuation", "done", "failed", "rejected"]

    for subdir in subdirs:
        tasks = list_tasks(subdir)
        for task in tasks:
            if task.get("id") == task_id:
                return task

    return None


def get_continuation_tasks(agent_name: str | None = None) -> list[dict[str, Any]]:
    """Get tasks that need continuation, optionally filtered by agent.

    Args:
        agent_name: Filter to tasks last worked on by this agent

    Returns:
        List of tasks needing continuation
    """
    tasks = list_tasks("needs_continuation")

    if agent_name:
        # Filter to tasks that were being worked on by this agent
        filtered = []
        for task in tasks:
            content = task.get("content", "")
            if f"LAST_AGENT: {agent_name}" in content or f"CLAIMED_BY: {agent_name}" in content:
                filtered.append(task)
        return filtered

    return tasks


def create_task(
    title: str,
    role: str,
    context: str,
    acceptance_criteria: list[str],
    priority: str = "P1",
    branch: str = "main",
    created_by: str = "human",
) -> Path:
    """Create a new task file in the incoming queue.

    Args:
        title: Task title
        role: Target role (implement, test, review)
        context: Background/context section content
        acceptance_criteria: List of acceptance criteria
        priority: P0, P1, or P2
        branch: Base branch to work from
        created_by: Who created the task

    Returns:
        Path to created task file
    """
    task_id = uuid4().hex[:8]
    filename = f"TASK-{task_id}.md"

    criteria_md = "\n".join(f"- [ ] {c}" for c in acceptance_criteria)

    content = f"""# [TASK-{task_id}] {title}

ROLE: {role}
PRIORITY: {priority}
BRANCH: {branch}
CREATED: {datetime.now().isoformat()}
CREATED_BY: {created_by}

## Context
{context}

## Acceptance Criteria
{criteria_md}
"""

    incoming_dir = get_queue_subdir("incoming")
    task_path = incoming_dir / filename

    task_path.write_text(content)
    return task_path


def _get_agent_state_dir() -> Path | None:
    """Get the agent's state directory (outside worktree).

    Returns None if not running as an agent (e.g., scheduler context).
    """
    orchestrator_dir = os.environ.get("ORCHESTRATOR_DIR")
    agent_name = os.environ.get("AGENT_NAME")
    if orchestrator_dir and agent_name:
        return Path(orchestrator_dir) / "agents" / agent_name
    return None


def write_task_marker(task_id: str, task_path: Path) -> None:
    """Write a task marker file in the agent's state directory.

    This links the agent to a specific task, allowing detection
    of stale resume attempts (task completed but worktree not reset).

    The marker is stored OUTSIDE the worktree so it's not affected
    by git operations (reset, checkout, etc.).

    Args:
        task_id: Task ID being worked on
        task_path: Path to the task file
    """
    state_dir = _get_agent_state_dir()
    if not state_dir:
        return  # Not running as agent

    marker_path = state_dir / "current_task.json"
    marker_data = {
        "task_id": task_id,
        "task_path": str(task_path),
        "started_at": datetime.now().isoformat(),
    }
    import json
    state_dir.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(marker_data, indent=2))


def read_task_marker() -> dict[str, Any] | None:
    """Read the task marker file from agent's state directory.

    Returns:
        Task marker data or None if not present
    """
    state_dir = _get_agent_state_dir()
    if not state_dir:
        return None

    marker_path = state_dir / "current_task.json"
    if not marker_path.exists():
        return None

    try:
        import json
        return json.loads(marker_path.read_text())
    except (IOError, json.JSONDecodeError):
        return None


def clear_task_marker() -> None:
    """Clear the task marker file from agent's state directory."""
    state_dir = _get_agent_state_dir()
    if not state_dir:
        return

    marker_path = state_dir / "current_task.json"
    if marker_path.exists():
        marker_path.unlink()


def is_task_still_valid(task_id: str) -> bool:
    """Check if a task is still valid to work on.

    A task is valid if it exists in 'claimed' or 'needs_continuation'.
    If it's in 'done', 'failed', or 'rejected', it should not be resumed.

    Args:
        task_id: Task ID to check

    Returns:
        True if task can still be worked on
    """
    # Check if task exists in active queues
    task = find_task_by_id(task_id, subdirs=["claimed", "needs_continuation"])
    return task is not None


def get_queue_status() -> dict[str, Any]:
    """Get overall queue status for monitoring.

    Returns:
        Dictionary with queue counts and task lists
    """
    return {
        "incoming": {
            "count": count_queue("incoming"),
            "tasks": list_tasks("incoming"),
        },
        "claimed": {
            "count": count_queue("claimed"),
            "tasks": list_tasks("claimed"),
        },
        "needs_continuation": {
            "count": count_queue("needs_continuation"),
            "tasks": list_tasks("needs_continuation"),
        },
        "done": {
            "count": count_queue("done"),
            "tasks": list_tasks("done")[-10:],  # Last 10 only
        },
        "failed": {
            "count": count_queue("failed"),
            "tasks": list_tasks("failed"),
        },
        "rejected": {
            "count": count_queue("rejected"),
            "tasks": list_tasks("rejected")[-10:],  # Last 10 only
        },
        "limits": get_queue_limits(),
        "open_prs": count_open_prs(),
    }
