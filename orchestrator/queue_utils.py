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

import yaml

from .config import get_queue_dir, get_queue_limits, is_db_enabled, get_orchestrator_dir


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


def get_projects_dir() -> Path:
    """Get the projects directory.

    Returns:
        Path to .orchestrator/shared/projects/
    """
    projects_dir = get_orchestrator_dir() / "shared" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    return projects_dir


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


def claim_task(
    role_filter: str | None = None,
    agent_name: str | None = None,
    from_queue: str = "incoming",
) -> dict[str, Any] | None:
    """Atomically claim a task from a queue.

    In DB mode, this enforces dependency checking - tasks with unresolved
    blocked_by entries cannot be claimed.

    Args:
        role_filter: Only claim tasks with this role (e.g., 'implement', 'test', 'breakdown')
        agent_name: Name of claiming agent (for logging in task)
        from_queue: Queue to claim from (default 'incoming', also supports 'breakdown')

    Returns:
        Task info dictionary if claimed, None if no suitable task
    """
    if is_db_enabled():
        from . import db
        db_task = db.claim_task(role_filter=role_filter, agent_name=agent_name, from_queue=from_queue)
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
    If the task belongs to a project, checks for project completion.

    Args:
        task_path: Path to the provisional task file
        validator: Name of the validator agent

    Returns:
        New path in done queue
    """
    task_path = Path(task_path)
    project_id = None

    if is_db_enabled():
        from . import db
        db_task = db.get_task_by_path(str(task_path))
        if db_task:
            db.accept_completion(db_task["id"], validator=validator)
            project_id = db_task.get("project_id")

    done_dir = get_queue_subdir("done")
    dest = done_dir / task_path.name

    # Append acceptance info
    with open(task_path, "a") as f:
        f.write(f"\nACCEPTED_AT: {datetime.now().isoformat()}\n")
        if validator:
            f.write(f"ACCEPTED_BY: {validator}\n")

    os.rename(task_path, dest)

    # Check for project completion
    if project_id and is_db_enabled():
        from . import db
        if db.check_project_completion(project_id):
            _write_project_file(db.get_project(project_id))

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
    project_id: str | None = None,
    queue: str = "incoming",
) -> Path:
    """Create a new task file in the specified queue.

    Args:
        title: Task title
        role: Target role (implement, test, review, breakdown)
        context: Background/context section content
        acceptance_criteria: List of acceptance criteria
        priority: P0, P1, or P2
        branch: Base branch to work from
        created_by: Who created the task
        blocked_by: Comma-separated list of task IDs that block this task
        project_id: Optional parent project ID
        queue: Queue to create in (default: incoming, can be 'breakdown')

    Returns:
        Path to created task file
    """
    task_id = uuid4().hex[:8]
    filename = f"TASK-{task_id}.md"

    criteria_md = "\n".join(f"- [ ] {c}" for c in acceptance_criteria)

    blocked_by_line = f"BLOCKED_BY: {blocked_by}\n" if blocked_by else ""
    project_line = f"PROJECT: {project_id}\n" if project_id else ""

    # If task belongs to a project, inherit branch from project
    if project_id and branch == "main" and is_db_enabled():
        from . import db
        project = db.get_project(project_id)
        if project and project.get("branch"):
            branch = project["branch"]

    content = f"""# [TASK-{task_id}] {title}

ROLE: {role}
PRIORITY: {priority}
BRANCH: {branch}
CREATED: {datetime.now().isoformat()}
CREATED_BY: {created_by}
{project_line}{blocked_by_line}
## Context
{context}

## Acceptance Criteria
{criteria_md}
"""

    queue_dir = get_queue_subdir(queue)
    task_path = queue_dir / filename

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
            project_id=project_id,
        )
        # Set queue status if not incoming
        if queue != "incoming":
            db.update_task(task_id, queue=queue)

    return task_path


def get_queue_status() -> dict[str, Any]:
    """Get overall queue status for monitoring.

    Returns:
        Dictionary with queue counts and task lists
    """
    queues = ["incoming", "claimed", "done", "failed", "rejected"]
    if is_db_enabled():
        queues.extend(["breakdown", "provisional", "escalated"])

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

    # Add project counts if DB enabled
    if is_db_enabled():
        from . import db
        result["projects"] = {
            "draft": len(db.list_projects("draft")),
            "active": len(db.list_projects("active")),
            "ready-for-pr": len(db.list_projects("ready-for-pr")),
            "complete": len(db.list_projects("complete")),
        }

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


# =============================================================================
# Project Operations
# =============================================================================


def create_project(
    title: str,
    description: str,
    created_by: str = "human",
    base_branch: str = "main",
    branch: str | None = None,
) -> dict[str, Any]:
    """Create a new project with both DB record and YAML file.

    Args:
        title: Project title
        description: Project description
        created_by: Who created the project
        base_branch: Base branch to create feature branch from
        branch: Feature branch name (auto-generated if not provided)

    Returns:
        Created project as dictionary
    """
    if not is_db_enabled():
        raise RuntimeError("Projects require database mode to be enabled")

    from . import db

    # Generate project ID
    project_id = f"PROJ-{uuid4().hex[:8]}"

    # Create in database
    project = db.create_project(
        project_id=project_id,
        title=title,
        description=description,
        branch=branch,
        base_branch=base_branch,
        created_by=created_by,
    )

    # Write YAML file for visibility
    _write_project_file(project)

    return project


def _write_project_file(project: dict[str, Any]) -> Path:
    """Write project data to YAML file.

    Args:
        project: Project dictionary

    Returns:
        Path to the YAML file
    """
    projects_dir = get_projects_dir()
    file_path = projects_dir / f"{project['id']}.yaml"

    # Convert to YAML-friendly format
    data = {
        "id": project["id"],
        "title": project["title"],
        "description": project.get("description"),
        "status": project.get("status", "draft"),
        "branch": project.get("branch"),
        "base_branch": project.get("base_branch", "main"),
        "created_at": project.get("created_at"),
        "created_by": project.get("created_by"),
        "completed_at": project.get("completed_at"),
    }

    with open(file_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    return file_path


def get_project(project_id: str) -> dict[str, Any] | None:
    """Get a project by ID.

    Args:
        project_id: Project identifier

    Returns:
        Project as dictionary or None if not found
    """
    if not is_db_enabled():
        return None

    from . import db
    return db.get_project(project_id)


def list_projects(status: str | None = None) -> list[dict[str, Any]]:
    """List projects, optionally filtered by status.

    Args:
        status: Filter by status (draft, active, complete, abandoned)

    Returns:
        List of project dictionaries
    """
    if not is_db_enabled():
        return []

    from . import db
    return db.list_projects(status=status)


def activate_project(project_id: str, create_branch: bool = True) -> dict[str, Any] | None:
    """Activate a project and optionally create its feature branch.

    Args:
        project_id: Project identifier
        create_branch: Whether to create the git branch

    Returns:
        Updated project or None if not found
    """
    if not is_db_enabled():
        return None

    from . import db

    project = db.get_project(project_id)
    if not project:
        return None

    # Create git branch if requested
    if create_branch and project.get("branch"):
        base = project.get("base_branch", "main")
        branch = project["branch"]
        try:
            subprocess.run(
                ["git", "checkout", "-b", branch, base],
                capture_output=True,
                check=True,
            )
            # Switch back to base branch
            subprocess.run(["git", "checkout", base], capture_output=True)
        except subprocess.CalledProcessError:
            pass  # Branch may already exist

    # Update status
    project = db.activate_project(project_id)
    _write_project_file(project)

    return project


def get_project_tasks(project_id: str) -> list[dict[str, Any]]:
    """Get all tasks belonging to a project.

    Args:
        project_id: Project identifier

    Returns:
        List of task dictionaries
    """
    if not is_db_enabled():
        return []

    from . import db
    return db.get_project_tasks(project_id)


def get_project_status(project_id: str) -> dict[str, Any] | None:
    """Get detailed project status including task breakdown.

    Args:
        project_id: Project identifier

    Returns:
        Status dictionary or None if project not found
    """
    if not is_db_enabled():
        return None

    from . import db

    project = db.get_project(project_id)
    if not project:
        return None

    tasks = db.get_project_tasks(project_id)

    # Count tasks by queue
    queue_counts = {}
    for task in tasks:
        queue = task.get("queue", "unknown")
        queue_counts[queue] = queue_counts.get(queue, 0) + 1

    # Check for blocked tasks
    blocked_tasks = [t for t in tasks if t.get("blocked_by")]

    return {
        "project": project,
        "task_count": len(tasks),
        "tasks_by_queue": queue_counts,
        "blocked_count": len(blocked_tasks),
        "tasks": tasks,
    }


def send_to_breakdown(
    title: str,
    description: str,
    context: str,
    created_by: str = "human",
    as_project: bool = True,
) -> dict[str, Any]:
    """Send work to the breakdown queue for decomposition.

    This is the main entry point for async work handoff.

    Args:
        title: Title of the work
        description: Description of what needs to be done
        context: Additional context/background
        created_by: Who created this
        as_project: If True, creates a project; if False, creates a single task

    Returns:
        Dictionary with project_id or task_id and file paths
    """
    if not is_db_enabled():
        raise RuntimeError("Breakdown queue requires database mode to be enabled")

    from . import db

    if as_project:
        # Create project
        project = create_project(
            title=title,
            description=description,
            created_by=created_by,
        )

        # Create initial breakdown task for the project
        task_path = create_task(
            title=f"Break down: {title}",
            role="breakdown",
            context=f"{description}\n\n{context}",
            acceptance_criteria=[
                "Decompose into right-sized tasks",
                "Create testing strategy task first",
                "Map dependencies between tasks",
                "Each task completable in <30 turns",
            ],
            priority="P1",
            created_by=created_by,
            project_id=project["id"],
            queue="breakdown",
        )

        return {
            "type": "project",
            "project_id": project["id"],
            "project": project,
            "breakdown_task": str(task_path),
        }
    else:
        # Create single task in breakdown queue
        task_path = create_task(
            title=title,
            role="breakdown",
            context=f"{description}\n\n{context}",
            acceptance_criteria=[
                "Break down into smaller tasks if needed",
                "Ensure clear acceptance criteria",
            ],
            priority="P1",
            created_by=created_by,
            queue="breakdown",
        )

        return {
            "type": "task",
            "task_path": str(task_path),
        }


def get_breakdowns_dir() -> Path:
    """Get the breakdowns directory.

    Returns:
        Path to .orchestrator/shared/breakdowns/
    """
    breakdowns_dir = get_orchestrator_dir() / "shared" / "breakdowns"
    breakdowns_dir.mkdir(parents=True, exist_ok=True)
    return breakdowns_dir


def list_pending_breakdowns() -> list[dict]:
    """List all pending breakdown files.

    Returns:
        List of breakdown info dicts with path, project_id, title, task_count
    """
    breakdowns_dir = get_breakdowns_dir()
    results = []

    for path in breakdowns_dir.glob("*.md"):
        content = path.read_text()

        # Parse metadata
        project_match = re.search(r'\*\*Project:\*\*\s*(\S+)', content)
        status_match = re.search(r'\*\*Status:\*\*\s*(\S+)', content)
        title_match = re.search(r'^# Breakdown:\s*(.+)$', content, re.MULTILINE)

        # Count tasks
        task_count = len(re.findall(r'^## Task \d+:', content, re.MULTILINE))

        status = status_match.group(1) if status_match else "unknown"

        if status == "pending_review":
            results.append({
                "path": path,
                "project_id": project_match.group(1) if project_match else None,
                "title": title_match.group(1) if title_match else path.stem,
                "task_count": task_count,
                "status": status,
            })

    return results


def approve_breakdown(identifier: str) -> dict[str, Any]:
    """Approve a breakdown and create tasks from it.

    Args:
        identifier: Project ID (PROJ-xxx) or breakdown filename

    Returns:
        Dict with created task info
    """
    breakdowns_dir = get_breakdowns_dir()

    # Find the breakdown file
    if identifier.startswith("PROJ-"):
        breakdown_path = breakdowns_dir / f"{identifier}-breakdown.md"
    else:
        breakdown_path = breakdowns_dir / f"{identifier}.md"
        if not breakdown_path.exists():
            breakdown_path = breakdowns_dir / identifier

    if not breakdown_path.exists():
        raise FileNotFoundError(f"Breakdown file not found: {breakdown_path}")

    content = breakdown_path.read_text()

    # Parse metadata
    project_match = re.search(r'\*\*Project:\*\*\s*(\S+)', content)
    branch_match = re.search(r'\*\*Branch:\*\*\s*(\S+)', content)
    status_match = re.search(r'\*\*Status:\*\*\s*(\S+)', content)

    project_id = project_match.group(1) if project_match else None
    branch = branch_match.group(1) if branch_match else "main"
    status = status_match.group(1) if status_match else "unknown"

    if status != "pending_review":
        raise ValueError(f"Breakdown is not pending review (status: {status})")

    # Parse tasks
    tasks = _parse_breakdown_tasks(content)

    if not tasks:
        raise ValueError("No tasks found in breakdown file")

    # Create tasks
    created_ids = []
    id_map = {}  # Map from task number to actual task ID

    for task in tasks:
        task_num = task["number"]
        title = task["title"]
        role = task.get("role", "implement")
        priority = task.get("priority", "P2")
        context = task.get("context", "")
        criteria = task.get("acceptance_criteria", [])
        depends_on = task.get("depends_on", [])

        # Resolve dependencies to actual task IDs
        blocked_by = None
        if depends_on:
            blocker_ids = []
            for dep_num in depends_on:
                if dep_num in id_map:
                    blocker_ids.append(id_map[dep_num])
            if blocker_ids:
                blocked_by = ",".join(blocker_ids)

        task_path = create_task(
            title=title,
            role=role,
            context=context,
            acceptance_criteria=criteria if criteria else ["Complete the task"],
            priority=priority,
            branch=branch,
            created_by="human",
            blocked_by=blocked_by,
            project_id=project_id,
            queue="incoming",
        )

        # Extract task ID from path
        task_id = task_path.stem.replace("TASK-", "")
        id_map[task_num] = task_id
        created_ids.append(task_id)

    # Update breakdown file status to approved
    updated_content = content.replace(
        "**Status:** pending_review",
        f"**Status:** approved\n**Approved:** {datetime.now().isoformat()}"
    )
    breakdown_path.write_text(updated_content)

    return {
        "breakdown_file": str(breakdown_path),
        "project_id": project_id,
        "tasks_created": len(created_ids),
        "task_ids": created_ids,
    }


def _parse_breakdown_tasks(content: str) -> list[dict]:
    """Parse tasks from a breakdown markdown file.

    Args:
        content: Markdown content of breakdown file

    Returns:
        List of task dicts with number, title, role, priority, context, criteria, depends_on
    """
    tasks = []

    # Split on task headers
    task_pattern = r'^## Task (\d+):\s*(.+)$'
    task_matches = list(re.finditer(task_pattern, content, re.MULTILINE))

    for i, match in enumerate(task_matches):
        task_num = int(match.group(1))
        title = match.group(2).strip()

        # Get content until next task or end
        start = match.end()
        end = task_matches[i + 1].start() if i + 1 < len(task_matches) else len(content)
        task_content = content[start:end]

        # Parse metadata
        role_match = re.search(r'\*\*Role:\*\*\s*(\S+)', task_content)
        priority_match = re.search(r'\*\*Priority:\*\*\s*(\S+)', task_content)
        depends_match = re.search(r'\*\*Depends on:\*\*\s*(.+)', task_content)

        role = role_match.group(1) if role_match else "implement"
        priority = priority_match.group(1) if priority_match else "P2"

        # Parse depends_on
        depends_on = []
        if depends_match:
            deps_str = depends_match.group(1).strip()
            if deps_str != "(none)":
                # Parse comma-separated numbers
                for dep in deps_str.split(","):
                    dep = dep.strip()
                    if dep.isdigit():
                        depends_on.append(int(dep))

        # Parse context
        context = ""
        context_match = re.search(r'### Context\s*\n\n(.+?)(?=\n###|\n---|\Z)', task_content, re.DOTALL)
        if context_match:
            context = context_match.group(1).strip()

        # Parse acceptance criteria
        criteria = []
        criteria_match = re.search(r'### Acceptance Criteria\s*\n\n(.+?)(?=\n###|\n---|\Z)', task_content, re.DOTALL)
        if criteria_match:
            criteria_text = criteria_match.group(1)
            for line in criteria_text.strip().split("\n"):
                # Match both checked and unchecked items
                item_match = re.match(r'^-\s*\[[ x]\]\s*(.+)$', line.strip())
                if item_match:
                    criteria.append(item_match.group(1))

        tasks.append({
            "number": task_num,
            "title": title,
            "role": role,
            "priority": priority,
            "context": context,
            "acceptance_criteria": criteria,
            "depends_on": depends_on,
        })

    return tasks
