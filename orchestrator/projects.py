"""Project management functions.

This module handles project CRUD operations and project-to-breakdown handoff.
"""

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
        branch: Feature branch name

    Returns:
        Created project as dictionary
    """
    project_id = f"PROJ-{uuid4().hex[:8]}"

    sdk = get_sdk()
    return sdk.projects.create(
        id=project_id,
        title=title,
        description=description,
        status="draft",
        branch=branch,
        base_branch=base_branch,
        created_by=created_by,
    )


def get_project(project_id: str) -> dict[str, Any] | None:
    """Get a project by ID via server API."""
    try:
        sdk = get_sdk()
        return sdk.projects.get(project_id)
    except Exception:
        return None


def list_projects(status: str | None = None) -> list[dict[str, Any]]:
    """List projects via server API, optionally filtered by status."""
    try:
        sdk = get_sdk()
        return sdk.projects.list(status=status)
    except Exception:
        return []


def activate_project(project_id: str, create_branch: bool = True) -> dict[str, Any] | None:
    """Activate a project and optionally create its feature branch."""
    project = get_project(project_id)
    if not project:
        return None

    if create_branch and project.get("branch"):
        base = project.get("base_branch", "main")
        branch = project["branch"]
        try:
            subprocess.run(
                ["git", "checkout", "-b", branch, base],
                capture_output=True,
                check=True,
            )
            subprocess.run(["git", "checkout", base], capture_output=True)
        except subprocess.CalledProcessError:
            pass  # Branch may already exist

    sdk = get_sdk()
    return sdk.projects.update(project_id, status="active")


def get_project_tasks(project_id: str) -> list[dict[str, Any]]:
    """Get all tasks belonging to a project."""
    sdk = get_sdk()
    return sdk.projects.get_tasks(project_id)


def get_project_status(project_id: str) -> dict[str, Any] | None:
    """Get detailed project status including task breakdown."""
    project = get_project(project_id)
    if not project:
        return None

    tasks = get_project_tasks(project_id)

    queue_counts = {}
    for task in tasks:
        queue = task.get("queue", "unknown")
        queue_counts[queue] = queue_counts.get(queue, 0) + 1

    blocked_tasks = [t for t in tasks if t.get("blocked_by")]

    return {
        "project": project,
        "task_count": len(tasks),
        "tasks_by_queue": queue_counts,
        "blocked_count": len(blocked_tasks),
        "tasks": tasks,
    }


def approve_project_via_flow(project_id: str) -> dict[str, Any]:
    """Execute the 'provisional -> done' flow transition for a project (human approval).

    This is called when a human approves a project that is in 'provisional' status
    (the review state after all child tasks complete and the PR is created).

    The flow engine:
    1. Loads the project's flow definition
    2. Finds the 'provisional -> done' transition
    3. Executes its steps (e.g. merge_project_pr)
    4. Updates the project status to the transition's target state ('done')

    Returns:
        dict with 'success': True/False and optional 'error' or 'new_status' keys.
    """
    from .config import find_parent_project
    from .flow import load_flow
    from .steps import execute_steps

    project = get_project(project_id)
    if not project:
        return {"success": False, "error": f"Project {project_id} not found"}

    flow_name = project.get("flow", "project")
    try:
        flow = load_flow(flow_name)
    except FileNotFoundError:
        if flow_name != "project":
            try:
                flow = load_flow("project")
            except FileNotFoundError:
                return {"success": False, "error": f"Flow '{flow_name}' and 'project' not found"}
        else:
            return {"success": False, "error": "Flow 'project' not found"}

    transitions = flow.get_transitions_from("provisional")
    if not transitions:
        return {
            "success": False,
            "error": f"No transition from 'provisional' in flow '{flow_name}'",
        }

    transition = transitions[0]
    parent_project_dir = find_parent_project()

    try:
        if transition.runs:
            execute_steps(transition.runs, project, {}, parent_project_dir)
    except Exception as e:
        return {"success": False, "error": f"Step execution failed: {e}"}

    sdk = get_sdk()
    sdk.projects.update(project_id, status=transition.to_state)

    return {"success": True, "new_status": transition.to_state}


def send_to_breakdown(
    title: str,
    description: str,
    context: str,
    created_by: str = "human",
    as_project: bool = True,
) -> dict[str, Any]:
    """Send work to the breakdown queue for decomposition."""
    from .tasks import create_task

    if as_project:
        project = create_project(
            title=title,
            description=description,
            created_by=created_by,
        )

        task_name = create_task(
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
            "breakdown_task": task_name,
        }
    else:
        task_name = create_task(
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
            "task_path": task_name,
        }
