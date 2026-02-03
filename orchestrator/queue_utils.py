"""Queue management with atomic operations and backpressure."""

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import get_queue_dir, get_queue_limits


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


def count_open_prs(author: str | None = None) -> int:
    """Count open pull requests via gh CLI.

    Args:
        author: Optional author to filter by (e.g., '@me' or username)

    Returns:
        Number of open PRs (0 if gh command fails)
    """
    try:
        cmd = ["gh", "pr", "list", "--state", "open", "--json", "number"]
        if author:
            cmd.extend(["--author", author])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return 0

        import json

        prs = json.loads(result.stdout)
        return len(prs)
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

    # Sort by priority (P0 first) then by created time
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    tasks.sort(key=lambda t: (priority_order.get(t.get("priority", "P2"), 2), t.get("created", "")))

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

    return {
        "path": task_path,
        "id": task_id,
        "title": title,
        "role": role_match.group(1).strip() if role_match else None,
        "priority": priority_match.group(1).strip() if priority_match else "P2",
        "branch": branch_match.group(1).strip() if branch_match else "main",
        "created": created_match.group(1).strip() if created_match else None,
        "created_by": created_by_match.group(1).strip() if created_by_match else None,
        "content": content,
    }


def claim_task(role_filter: str | None = None, agent_name: str | None = None) -> dict[str, Any] | None:
    """Atomically claim a task from incoming queue.

    Uses os.rename which is atomic on POSIX to prevent race conditions.

    Args:
        role_filter: Only claim tasks with this role (e.g., 'implement', 'test')
        agent_name: Name of claiming agent (for logging in task)

    Returns:
        Task info dictionary if claimed, None if no suitable task
    """
    incoming_dir = get_queue_subdir("incoming")
    claimed_dir = get_queue_subdir("claimed")

    tasks = list_tasks("incoming")

    for task in tasks:
        # Filter by role if specified
        if role_filter and task.get("role") != role_filter:
            continue

        source = task["path"]
        dest = claimed_dir / source.name

        try:
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
        "done": {
            "count": count_queue("done"),
            "tasks": list_tasks("done")[-10:],  # Last 10 only
        },
        "failed": {
            "count": count_queue("failed"),
            "tasks": list_tasks("failed"),
        },
        "limits": get_queue_limits(),
        "open_prs": count_open_prs(),
    }
