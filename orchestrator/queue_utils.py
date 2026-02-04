"""Queue management with atomic operations and backpressure.

Supports both file-based (default) and SQLite database backends.
The backend is selected via the `database.enabled` setting in agents.yaml.
"""

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import get_queue_dir, get_queue_limits, is_db_enabled


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
    """Count tasks in a queue.

    Args:
        subdir: One of 'incoming', 'claimed', 'done', 'failed', 'provisional'

    Returns:
        Number of tasks
    """
    if is_db_enabled():
        from . import db
        return db.count_tasks(subdir)

    # File-based fallback
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
        subdir: One of 'incoming', 'claimed', 'done', 'failed', 'provisional'

    Returns:
        List of task dictionaries with path, id, role, priority, created, title
    """
    if is_db_enabled():
        from . import db
        db_tasks = db.list_tasks(queue=subdir)
        # Convert DB format to file format for compatibility
        return [_db_task_to_file_format(t) for t in db_tasks]

    # File-based fallback
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


def _db_task_to_file_format(db_task: dict[str, Any]) -> dict[str, Any]:
    """Convert a database task record to file-format task dict.

    Args:
        db_task: Task from database

    Returns:
        Task dict compatible with file-based format
    """
    file_path = Path(db_task.get("file_path", ""))

    # Read content from file if it exists
    content = ""
    title = db_task["id"]
    if file_path.exists():
        try:
            content = file_path.read_text()
            # Extract title from content
            title_match = re.search(r"^#\s*\[TASK-[^\]]+\]\s*(.+)$", content, re.MULTILINE)
            if title_match:
                title = title_match.group(1).strip()
        except IOError:
            pass

    return {
        "path": file_path,
        "id": db_task["id"],
        "title": title,
        "role": db_task.get("role"),
        "priority": db_task.get("priority", "P2"),
        "branch": db_task.get("branch", "main"),
        "created": db_task.get("created_at"),
        "created_by": None,
        "content": content,
        # Additional DB fields
        "blocked_by": db_task.get("blocked_by"),
        "claimed_by": db_task.get("claimed_by"),
        "attempt_count": db_task.get("attempt_count", 0),
        "commits_count": db_task.get("commits_count", 0),
        "has_plan": db_task.get("has_plan", False),
    }


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
    blocked_by_match = re.search(r"^BLOCKED_BY:\s*(.+)$", content, re.MULTILINE)

    return {
        "path": task_path,
        "id": task_id,
        "title": title,
        "role": role_match.group(1).strip() if role_match else None,
        "priority": priority_match.group(1).strip() if priority_match else "P2",
        "branch": branch_match.group(1).strip() if branch_match else "main",
        "created": created_match.group(1).strip() if created_match else None,
        "created_by": created_by_match.group(1).strip() if created_by_match else None,
        "blocked_by": blocked_by_match.group(1).strip() if blocked_by_match else None,
        "content": content,
    }


def claim_task(role_filter: str | None = None, agent_name: str | None = None) -> dict[str, Any] | None:
    """Atomically claim a task from incoming queue.

    In DB mode, this enforces dependency checking - tasks with unresolved
    blocked_by entries cannot be claimed.

    Args:
        role_filter: Only claim tasks with this role (e.g., 'implement', 'test')
        agent_name: Name of claiming agent (for logging in task)

    Returns:
        Task info dictionary if claimed, None if no suitable task
    """
    if is_db_enabled():
        from . import db
        db_task = db.claim_task(role_filter=role_filter, agent_name=agent_name)
        if db_task:
            # Also update the file with claim info
            file_path = Path(db_task["file_path"])
            if file_path.exists() and agent_name:
                try:
                    with open(file_path, "a") as f:
                        f.write(f"\nCLAIMED_BY: {agent_name}\n")
                        f.write(f"CLAIMED_AT: {datetime.now().isoformat()}\n")
                except IOError:
                    pass
            return _db_task_to_file_format(db_task)
        return None

    # File-based fallback
    incoming_dir = get_queue_subdir("incoming")
    claimed_dir = get_queue_subdir("claimed")

    tasks = list_tasks("incoming")

    for task in tasks:
        # Filter by role if specified
        if role_filter and task.get("role") != role_filter:
            continue

        # Check dependencies (file-based simple check)
        if task.get("blocked_by"):
            # Skip blocked tasks in file mode
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

    Note: In DB mode with validation enabled, use submit_completion() instead
    to go through the provisional queue for validation.

    Args:
        task_path: Path to the claimed task file
        result: Optional result summary to append

    Returns:
        New path in done queue
    """
    task_path = Path(task_path)

    if is_db_enabled():
        from . import db
        db_task = db.get_task_by_path(str(task_path))
        if db_task:
            db.accept_completion(db_task["id"])

    done_dir = get_queue_subdir("done")
    dest = done_dir / task_path.name

    # Append completion info
    with open(task_path, "a") as f:
        f.write(f"\nCOMPLETED_AT: {datetime.now().isoformat()}\n")
        if result:
            f.write(f"\n## Result\n{result}\n")

    os.rename(task_path, dest)
    return dest


def submit_completion(
    task_path: Path | str,
    commits_count: int = 0,
    turns_used: int | None = None,
) -> Path | None:
    """Submit a task for validation (move to provisional queue).

    The task stays in provisional until a validator accepts or rejects it.
    Only available in DB mode - in file mode, falls back to complete_task().

    Args:
        task_path: Path to the claimed task file
        commits_count: Number of commits made during implementation
        turns_used: Number of Claude turns used

    Returns:
        New path in provisional queue, or None if DB not enabled
    """
    task_path = Path(task_path)

    if not is_db_enabled():
        # Fall back to direct completion in file mode
        return complete_task(task_path, f"commits={commits_count}, turns={turns_used}")

    from . import db

    db_task = db.get_task_by_path(str(task_path))
    if not db_task:
        # Task not in DB, fall back to file-based
        return complete_task(task_path, f"commits={commits_count}, turns={turns_used}")

    # Update DB to provisional
    db.submit_completion(db_task["id"], commits_count=commits_count, turns_used=turns_used)

    # Move file to provisional directory
    provisional_dir = get_queue_subdir("provisional")
    dest = provisional_dir / task_path.name

    # Append submission info
    with open(task_path, "a") as f:
        f.write(f"\nSUBMITTED_AT: {datetime.now().isoformat()}\n")
        f.write(f"COMMITS_COUNT: {commits_count}\n")
        if turns_used:
            f.write(f"TURNS_USED: {turns_used}\n")

    os.rename(task_path, dest)
    return dest


def accept_completion(
    task_path: Path | str,
    validator: str | None = None,
) -> Path:
    """Accept a provisional task and move it to done.

    Called by the validator when a task passes validation.

    Args:
        task_path: Path to the provisional task file
        validator: Name of the validator agent

    Returns:
        New path in done queue
    """
    task_path = Path(task_path)

    if is_db_enabled():
        from . import db
        db_task = db.get_task_by_path(str(task_path))
        if db_task:
            db.accept_completion(db_task["id"], validator=validator)

    done_dir = get_queue_subdir("done")
    dest = done_dir / task_path.name

    # Append acceptance info
    with open(task_path, "a") as f:
        f.write(f"\nACCEPTED_AT: {datetime.now().isoformat()}\n")
        if validator:
            f.write(f"ACCEPTED_BY: {validator}\n")

    os.rename(task_path, dest)
    return dest


def reject_completion(
    task_path: Path | str,
    reason: str,
    validator: str | None = None,
) -> Path:
    """Reject a provisional task and move it back to incoming for retry.

    Called by the validator when a task fails validation (e.g., no commits).
    The task's attempt_count is incremented.

    Args:
        task_path: Path to the provisional task file
        reason: Rejection reason
        validator: Name of the validator agent

    Returns:
        New path in incoming queue
    """
    task_path = Path(task_path)
    attempt_count = 0

    if is_db_enabled():
        from . import db
        db_task = db.get_task_by_path(str(task_path))
        if db_task:
            updated = db.reject_completion(db_task["id"], reason=reason, validator=validator)
            if updated:
                attempt_count = updated.get("attempt_count", 0)

    incoming_dir = get_queue_subdir("incoming")
    dest = incoming_dir / task_path.name

    # Append rejection info
    with open(task_path, "a") as f:
        f.write(f"\nREJECTED_AT: {datetime.now().isoformat()}\n")
        f.write(f"REJECTION_REASON: {reason}\n")
        f.write(f"ATTEMPT_COUNT: {attempt_count}\n")
        if validator:
            f.write(f"REJECTED_BY: {validator}\n")

    os.rename(task_path, dest)
    return dest


def escalate_to_planning(task_path: Path | str, plan_id: str) -> Path:
    """Escalate a failed task to planning.

    Creates a planning task to break down the original task into micro-tasks.
    Called when a task has exceeded max_attempts_before_planning.

    Args:
        task_path: Path to the task file being escalated
        plan_id: ID of the new planning task

    Returns:
        New path in escalated queue
    """
    task_path = Path(task_path)

    if is_db_enabled():
        from . import db
        db_task = db.get_task_by_path(str(task_path))
        if db_task:
            db.escalate_to_planning(db_task["id"], plan_id=plan_id)

    escalated_dir = get_queue_subdir("escalated")
    dest = escalated_dir / task_path.name

    # Append escalation info
    with open(task_path, "a") as f:
        f.write(f"\nESCALATED_AT: {datetime.now().isoformat()}\n")
        f.write(f"PLAN_ID: {plan_id}\n")

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

    if is_db_enabled():
        from . import db
        db_task = db.get_task_by_path(str(task_path))
        if db_task:
            db.fail_task(db_task["id"], error=error)

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

    if is_db_enabled():
        from . import db
        db_task = db.get_task_by_path(str(task_path))
        if db_task:
            db.update_task(
                db_task["id"],
                queue="incoming",
                claimed_by=None,
                claimed_at=None,
            )

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
    blocked_by: str | None = None,
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
        blocked_by: Comma-separated list of task IDs that block this task

    Returns:
        Path to created task file
    """
    task_id = uuid4().hex[:8]
    filename = f"TASK-{task_id}.md"

    criteria_md = "\n".join(f"- [ ] {c}" for c in acceptance_criteria)

    blocked_by_line = f"BLOCKED_BY: {blocked_by}\n" if blocked_by else ""

    content = f"""# [TASK-{task_id}] {title}

ROLE: {role}
PRIORITY: {priority}
BRANCH: {branch}
CREATED: {datetime.now().isoformat()}
CREATED_BY: {created_by}
{blocked_by_line}
## Context
{context}

## Acceptance Criteria
{criteria_md}
"""

    incoming_dir = get_queue_subdir("incoming")
    task_path = incoming_dir / filename

    task_path.write_text(content)

    # Also create in DB if enabled
    if is_db_enabled():
        from . import db
        db.create_task(
            task_id=task_id,
            file_path=str(task_path),
            priority=priority,
            role=role,
            branch=branch,
            blocked_by=blocked_by,
        )

    return task_path


def get_queue_status() -> dict[str, Any]:
    """Get overall queue status for monitoring.

    Returns:
        Dictionary with queue counts and task lists
    """
    queues = ["incoming", "claimed", "done", "failed", "rejected"]
    if is_db_enabled():
        queues.extend(["provisional", "escalated"])

    result = {}
    for q in queues:
        tasks = list_tasks(q)
        result[q] = {
            "count": len(tasks),
            "tasks": tasks[-10:] if q in ("done", "rejected") else tasks,
        }

    result["limits"] = get_queue_limits()
    result["open_prs"] = count_open_prs()
    result["db_enabled"] = is_db_enabled()

    return result


def get_task_by_id(task_id: str) -> dict[str, Any] | None:
    """Get a task by its ID.

    Args:
        task_id: Task identifier (e.g., 'abc12345')

    Returns:
        Task dict or None if not found
    """
    if is_db_enabled():
        from . import db
        db_task = db.get_task(task_id)
        if db_task:
            return _db_task_to_file_format(db_task)
        return None

    # File-based: search all queues
    for subdir in ["incoming", "claimed", "done", "failed", "rejected"]:
        path = get_queue_subdir(subdir)
        for task_file in path.glob(f"*{task_id}*.md"):
            task_info = parse_task_file(task_file)
            if task_info and task_info["id"] == task_id:
                return task_info

    return None
