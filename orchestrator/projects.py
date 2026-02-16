"""Project management functions.

This module handles project CRUD operations and project-to-breakdown handoff.
"""

import re
import subprocess
from typing import Any
from uuid import uuid4

from .sdk import get_sdk


def create_project(
    title: str,
    description: str,
    created_by: str = "human",
    base_branch: str = "main",
    branch: str | None = None,
) -> dict[str, Any]:
    """Create a new project via server API.

    Args:
        title: Project title
        description: Project description
        created_by: Who created the project
        base_branch: Base branch to create feature branch from
        branch: Feature branch name (required, will be validated)

    Returns:
        Created project as dictionary

    Raises:
        ValueError: If branch is None or validation fails
    """
    # Validate branch is required
    if not branch:
        raise ValueError("branch is required for project creation")

    # Validate branch exists on origin
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", branch],
            capture_output=True,
            text=True,
            check=False,
        )
        branch_exists = bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        branch_exists = False

    # Raise error if branch doesn't exist on origin
    if not branch_exists:
        raise ValueError(f"Branch '{branch}' does not exist on origin")

    # Check if we're on a feature branch but base_branch is main
    try:
        current_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        if base_branch == "main" and current_branch != "main" and not current_branch.startswith("agent/"):
            print(f"Warning: Creating project with base_branch='main' while on feature branch '{current_branch}'")
            print(f"Consider using base_branch='{current_branch}' instead")
    except subprocess.CalledProcessError:
        pass  # Ignore git errors

    # Generate project ID
    project_id = f"PROJ-{uuid4().hex[:8]}"

    sdk = get_sdk()
    project = sdk.projects.create(
        id=project_id,
        title=title,
        description=description,
        status="draft",
        branch=branch,
        base_branch=base_branch,
        created_by=created_by,
    )

    return project


def get_project(project_id: str) -> dict[str, Any] | None:
    """Get a project by ID via server API.

    Args:
        project_id: Project identifier

    Returns:
        Project as dictionary or None if not found
    """
    try:
        sdk = get_sdk()
        return sdk.projects.get(project_id)
    except Exception:
        return None


def list_projects(status: str | None = None) -> list[dict[str, Any]]:
    """List projects via server API, optionally filtered by status.

    Args:
        status: Filter by status (draft, active, completed, archived)

    Returns:
        List of project dictionaries
    """
    try:
        sdk = get_sdk()
        return sdk.projects.list(status=status)
    except Exception:
        return []


def activate_project(project_id: str, create_branch: bool = True) -> dict[str, Any] | None:
    """Activate a project and optionally create its feature branch.

    Args:
        project_id: Project identifier
        create_branch: Whether to create the git branch

    Returns:
        Updated project or None if not found
    """
    project = get_project(project_id)
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

    # Update status via API
    sdk = get_sdk()
    project = sdk.projects.update(project_id, status="active")

    return project


def get_project_tasks(project_id: str) -> list[dict[str, Any]]:
    """Get all tasks belonging to a project.

    Args:
        project_id: Project identifier

    Returns:
        List of task dictionaries
    """
    sdk = get_sdk()
    return sdk.projects.get_tasks(project_id)


def get_project_status(project_id: str) -> dict[str, Any] | None:
    """Get detailed project status including task breakdown.

    Args:
        project_id: Project identifier

    Returns:
        Status dictionary or None if project not found
    """
    project = get_project(project_id)
    if not project:
        return None

    tasks = get_project_tasks(project_id)

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
    # Import here to avoid circular dependency
    from .tasks import create_task

    if as_project:
        # Generate branch name from title - sanitize to valid git ref characters
        slug = re.sub(r'[^a-z0-9_/-]', '', title.lower().replace(' ', '-'))[:50]
        branch_name = f"feature/{slug}"

        # Create project
        project = create_project(
            title=title,
            description=description,
            created_by=created_by,
            branch=branch_name,
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
