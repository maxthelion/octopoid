"""Reusable setup helpers for task lifecycle flow tests."""

import uuid
from typing import Optional, Tuple, Dict, Any

from octopoid_sdk import OctopoidSDK


def make_task_id() -> str:
    """Generate a unique task ID."""
    return f"TEST-{uuid.uuid4().hex[:8]}"


def create_task(
    sdk: OctopoidSDK,
    role: str = "implement",
    branch: str = "main",
    task_type: Optional[str] = None,
    priority: Optional[str] = None,
    blocked_by: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Create a task and return it. Returns the created task dict."""
    task_id = make_task_id()
    extra: Dict[str, Any] = {}
    if task_type:
        extra["type"] = task_type
    if priority:
        extra["priority"] = priority
    if blocked_by:
        extra["blocked_by"] = blocked_by
    extra.update(kwargs)

    return sdk.tasks.create(
        id=task_id,
        file_path=f".octopoid/tasks/{task_id}.md",
        title=f"Flow test {task_id}",
        role=role,
        branch=branch,
        **extra,
    )


def create_and_claim(
    sdk: OctopoidSDK,
    orchestrator_id: str,
    role: str = "implement",
    branch: str = "main",
    task_type: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Create a task and claim it. Returns (task_id, claimed_task)."""
    task = create_task(sdk, role=role, branch=branch, task_type=task_type)
    task_id = task["id"]

    claim_kwargs: Dict[str, Any] = {
        "orchestrator_id": orchestrator_id,
        "agent_name": "test-agent",
        "role_filter": role,
    }
    if task_type:
        claim_kwargs["type_filter"] = task_type

    claimed = sdk.tasks.claim(**claim_kwargs)
    return task_id, claimed


def create_provisional(
    sdk: OctopoidSDK,
    orchestrator_id: str,
    role: str = "implement",
    branch: str = "main",
) -> str:
    """Create a task and advance it to provisional. Returns task_id."""
    task_id, _ = create_and_claim(sdk, orchestrator_id, role=role, branch=branch)
    sdk.tasks.submit(task_id, commits_count=1, turns_used=5)
    return task_id
