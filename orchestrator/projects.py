"""Project management functions.

This module handles project CRUD operations and project-to-breakdown handoff.
"""

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .config import get_orchestrator_dir
from .sdk import get_sdk


def get_projects_dir() -> Path:
    """Get the projects directory.

    Returns:
        Path to .octopoid/shared/projects/
    """
    projects_dir = get_orchestrator_dir() / "shared" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    return projects_dir


def create_project(
    title: str,
    description: str,
    created_by: str = "human",
    base_branch: str = "main",
    branch: str | None = None,
) -> dict[str, Any]:
    """Create a new project via API with local YAML file.

    Args:
        title: Project title
        description: Project description
        created_by: Who created the project
        base_branch: Base branch to create feature branch from
        branch: Feature branch name (auto-generated if not provided)

    Returns:
        Created project as dictionary
    """
    # Generate project ID
    project_id = f"PROJ-{uuid4().hex[:8]}"

    try:
        sdk = get_sdk()
        # Note: SDK projects API needs to be implemented in the SDK client
        # For now, create local YAML file only
        # TODO: Add sdk.projects.create() when server endpoint exists

        project = {
            "id": project_id,
            "title": title,
            "description": description,
            "status": "draft",
            "branch": branch,
            "base_branch": base_branch,
            "created_at": datetime.now().isoformat(),
            "created_by": created_by,
        }

        # Write YAML file for visibility
        _write_project_file(project)

        return project
    except Exception as e:
        print(f"Warning: Failed to create project: {e}")
        raise


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
    """Get a project by ID via API.

    Args:
        project_id: Project identifier

    Returns:
        Project as dictionary or None if not found
    """
    try:
        sdk = get_sdk()
        # Note: SDK projects API needs to be implemented
        # For now, read from local YAML file
        # TODO: Use sdk.projects.get() when server endpoint exists

        projects_dir = get_projects_dir()
        file_path = projects_dir / f"{project_id}.yaml"

        if not file_path.exists():
            return None

        with open(file_path) as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"Warning: Failed to get project {project_id}: {e}")
        return None


def list_projects(status: str | None = None) -> list[dict[str, Any]]:
    """List projects via API, optionally filtered by status.

    Args:
        status: Filter by status (draft, active, complete, abandoned)

    Returns:
        List of project dictionaries
    """
    try:
        sdk = get_sdk()
        # Note: SDK projects API needs to be implemented
        # For now, read from local YAML files
        # TODO: Use sdk.projects.list() when server endpoint exists

        projects_dir = get_projects_dir()
        if not projects_dir.exists():
            return []

        projects = []
        for file_path in projects_dir.glob("PROJ-*.yaml"):
            with open(file_path) as f:
                project = yaml.safe_load(f)
                if status is None or project.get("status") == status:
                    projects.append(project)

        return projects
    except Exception as e:
        print(f"Warning: Failed to list projects: {e}")
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

    # Update status
    project["status"] = "active"
    _write_project_file(project)

    return project


def get_project_tasks(project_id: str) -> list[dict[str, Any]]:
    """Get all tasks belonging to a project.

    Args:
        project_id: Project identifier

    Returns:
        List of task dictionaries
    """
    return []


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
