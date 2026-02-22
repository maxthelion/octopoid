"""Task lifecycle, CRUD, and query operations.

All task state transitions use the Octopoid SDK API.
Task files on disk are supplementary — the API is the source of truth.
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import (
    ACTIVE_QUEUES,
    get_base_branch,
    get_tasks_file_dir,
)
from .sdk import get_sdk, get_orchestrator_id
from .task_logger import get_task_logger

def _transition(
    task_id: str,
    queue: str,
    *,
    cleanup_worktree: bool = False,
    push_commits: bool = False,
    log_fn=None,
    **sdk_kwargs
) -> dict:
    """Transition a task to a new queue with optional cleanup and logging."""
    sdk = get_sdk()
    result = sdk.tasks.update(task_id, queue=queue, **sdk_kwargs)

    if log_fn:
        log_fn(get_task_logger(task_id))

    if cleanup_worktree:
        from .git_utils import cleanup_task_worktree
        cleanup_task_worktree(task_id, push_commits=push_commits)

    return result

def claim_task(
    role_filter: str | None = None,
    agent_name: str | None = None,
    from_queue: str = "incoming",
    type_filter: str | None = None,
) -> dict[str, Any] | None:
    """Atomically claim a task from the API server (with lease-based coordination)."""
    from .backpressure import get_queue_limits

    sdk = get_sdk()
    orchestrator_id = get_orchestrator_id()
    limits = get_queue_limits()

    # 1-hour lease (agents take 5-30 min, no renewal mechanism yet)
    claim_kwargs: dict[str, Any] = dict(
        orchestrator_id=orchestrator_id,
        agent_name=agent_name or "unknown",
        role_filter=role_filter,
        type_filter=type_filter,
        max_claimed=limits.get("max_claimed"),
        lease_duration_seconds=3600,
    )
    if from_queue != "incoming":
        claim_kwargs["queue"] = from_queue
    task = sdk.tasks.claim(**claim_kwargs)

    if task is None:
        return None

    file_path_str = task.get("file_path")
    if file_path_str:
        tasks_dir = get_tasks_file_dir()
        task_file = tasks_dir / Path(file_path_str).name
        if task_file.exists():
            task["file_path"] = str(task_file)
            task["content"] = task_file.read_text()

    task_id = task.get("id")
    if task_id:
        logger = get_task_logger(task_id)
        attempt = task.get("attempt_count", 0)
        logger.log_claimed(
            claimed_by=orchestrator_id,
            agent=agent_name or "unknown",
            attempt=attempt,
        )

    return task

def unclaim_task(task_id: str) -> dict:
    """Return a claimed task to the incoming queue."""
    return _transition(task_id, "incoming", claimed_by=None)

def complete_task(task_id: str) -> dict:
    """Move a task to done queue. For review flow, use submit_completion()."""
    from .task_notes import cleanup_task_notes

    sdk = get_sdk()
    result = sdk.tasks.accept(task_id, accepted_by="complete_task")
    cleanup_task_notes(task_id)
    return result

def submit_completion(
    task_id: str,
    commits_count: int = 0,
    turns_used: int | None = None,
) -> dict | None:
    """Submit a task for review via API (moves to provisional queue)."""
    sdk = get_sdk()

    task = sdk.tasks.get(task_id)
    if not task:
        print(f"Warning: Task {task_id} not found in API")
        return None

    attempt_count = task.get("attempt_count", 0)
    rejection_count = task.get("rejection_count", 0)
    previously_claimed = attempt_count > 0 or rejection_count > 0

    if commits_count == 0 and previously_claimed:
        return reject_completion(
            task_id,
            reason="No commits made. Read the task file and rejection feedback, then implement the required changes.",
            accepted_by="submit_completion",
        )

    parts = [f"{commits_count} commit(s)" if commits_count > 0 else "No commits"]
    if turns_used:
        parts.append(f"{turns_used} turn(s)")
    execution_notes = ". ".join(parts) + "."

    result = sdk.tasks.submit(
        task_id=task_id,
        commits_count=commits_count,
        turns_used=turns_used or 0,
        execution_notes=execution_notes,
    )

    logger = get_task_logger(task_id)
    logger.log_submitted(
        commits=commits_count,
        turns=turns_used or 0,
    )

    return result

def accept_completion(
    task_id: str,
    accepted_by: str | None = None,
) -> dict:
    """Accept a provisional task via API (moves to done queue)."""
    from .task_notes import cleanup_task_notes
    from .task_thread import cleanup_thread

    sdk = get_sdk()
    result = sdk.tasks.accept(task_id, accepted_by=accepted_by or "unknown")

    logger = get_task_logger(task_id)
    logger.log_accepted(accepted_by=accepted_by or "unknown")

    from .git_utils import cleanup_task_worktree
    cleanup_task_worktree(task_id, push_commits=True)

    cleanup_task_notes(task_id)
    cleanup_thread(task_id)
    return result

def reject_completion(
    task_id: str,
    reason: str,
    accepted_by: str | None = None,
) -> dict:
    """Reject a provisional task via API (moves back to incoming for retry)."""
    sdk = get_sdk()

    updated_task = sdk.tasks.reject(
        task_id=task_id,
        reason=reason,
        rejected_by=accepted_by
    )

    logger = get_task_logger(task_id)
    logger.log_rejected(
        reason=reason,
        rejected_by=accepted_by or "unknown",
    )

    from .git_utils import cleanup_task_worktree
    cleanup_task_worktree(task_id, push_commits=True)

    return updated_task

def review_reject_task(
    task_id: str,
    feedback: str,
    rejected_by: str | None = None,
    max_rejections: int = 3,
) -> tuple[str, str]:
    """Reject a provisional task with review feedback.

    Posts the feedback as a rejection message on the task thread instead of
    rewriting the task file. The original task instructions are preserved
    unmodified; the next agent receives them alongside the full message thread.
    """
    from .task_thread import post_message

    rejection_count = 0
    try:
        sdk = get_sdk()
        api_task = sdk.tasks.get(task_id)
        if api_task:
            rejection_count = (api_task.get("rejection_count") or 0) + 1
    except Exception:
        pass

    escalated = rejection_count >= max_rejections

    # Post feedback as a thread message — do NOT rewrite the task file
    try:
        post_message(
            task_id,
            role="rejection",
            content=feedback,
            author=rejected_by or "reviewer",
        )
    except Exception as e:
        print(f"Warning: Failed to post rejection message for task {task_id}: {e}")

    try:
        sdk = get_sdk()
        if escalated:
            sdk.tasks.update(task_id, queue="escalated", claimed_by=None)
        else:
            sdk.tasks.reject(
                task_id=task_id,
                reason=feedback[:500],
                rejected_by=rejected_by,
            )

        logger = get_task_logger(task_id)
        if escalated:
            logger.log_requeued(
                from_queue="provisional",
                to_queue="escalated",
                reason=f"Escalated after {rejection_count} rejections",
            )
        else:
            logger.log_rejected(
                reason=feedback[:200] + ("..." if len(feedback) > 200 else ""),
                rejected_by=rejected_by or "reviewer",
            )
    except Exception as e:
        print(f"Warning: Failed to update task {task_id} via API: {e}")

    if escalated:
        try:
            from .sdk import get_sdk
            sdk = get_sdk()
            body = (
                f"Task has been rejected {rejection_count} times by reviewers. "
                f"Human attention required.\n\nLatest feedback:\n{feedback[:1000]}"
            )
            sdk.messages.create(
                task_id=task_id,
                from_actor=rejected_by or "gatekeeper",
                type="warning",
                content=body,
                to_actor="human",
            )
        except Exception as e:
            print(f"Warning: Failed to post escalation message for task {task_id}: {e}")

    action = "escalated" if escalated else "rejected"

    from .git_utils import cleanup_task_worktree
    cleanup_task_worktree(task_id, push_commits=True)

    return (task_id, action)

def get_review_feedback(task_id: str) -> str | None:
    """Extract review feedback sections from a task's markdown file."""
    task = get_task_by_id(task_id)
    if not task:
        return None

    content = task.get("content", "")
    if not content:
        return None

    new_sections = re.findall(
        r'## Rejection Notice.*?\n(.*?)(?=\n## |\Z)',
        content,
        re.DOTALL,
    )

    if new_sections:
        return "\n\n---\n\n".join(section.strip() for section in new_sections)

    legacy_sections = re.findall(
        r'## Review Feedback \(rejection #\d+\)\s*\n(.*?)(?=\n## |\Z)',
        content,
        re.DOTALL,
    )

    if not legacy_sections:
        return None

    return "\n\n---\n\n".join(section.strip() for section in legacy_sections)

def fail_task(task_id: str, error: str) -> dict:
    """Fail a task (moves to failed queue with cleanup)."""
    error_summary = error[:200] + ("..." if len(error) > 200 else "")
    return _transition(
        task_id,
        "failed",
        cleanup_worktree=True,
        log_fn=lambda l: l.log_failed(error=error_summary)
    )

def reject_task(
    task_id: str,
    reason: str,
    details: str | None = None,
    rejected_by: str | None = None,
) -> dict:
    """Reject a task (moves to rejected queue)."""
    sdk = get_sdk()
    return sdk.tasks.reject(
        task_id=task_id,
        reason=reason,
        details=details,
        rejected_by=rejected_by,
    )

def retry_task(task_id: str) -> dict:
    """Retry a failed task (moves back to incoming)."""
    sdk = get_sdk()
    return sdk.tasks.update(task_id, queue="incoming", claimed_by=None, claimed_at=None)

def reset_task(task_id: str) -> dict[str, Any]:
    """Reset a task to incoming via API with clean state."""
    try:
        sdk = get_sdk()

        # Get current task to find file path
        task = sdk.tasks.get(task_id)
        if not task:
            raise LookupError(f"Task {task_id} not found in API")

        old_queue = task.get("queue", "unknown")

        # Reset task state via API
        sdk.tasks.update(
            task_id,
            queue="incoming",
            claimed_by=None,
            claimed_at=None,
            checks=None,
            check_results=None,
            rejection_count=0,
        )

        return {
            "task_id": task_id,
            "old_queue": old_queue,
            "new_queue": "incoming",
            "action": "reset",
        }
    except Exception as e:
        raise RuntimeError(f"Failed to reset task {task_id}: {e}")

def hold_task(task_id: str) -> dict[str, Any]:
    """Hold a task (moves to escalated queue)."""
    try:
        sdk = get_sdk()

        # Get current task to find file path
        task = sdk.tasks.get(task_id)
        if not task:
            raise LookupError(f"Task {task_id} not found in API")

        old_queue = task.get("queue", "unknown")

        # Move task to escalated queue via API
        sdk.tasks.update(
            task_id,
            queue="escalated",
            claimed_by=None,
            claimed_at=None,
            checks=None,
            check_results=None,
        )

        return {
            "task_id": task_id,
            "old_queue": old_queue,
            "new_queue": "escalated",
            "action": "held",
        }
    except Exception as e:
        raise RuntimeError(f"Failed to hold task {task_id}: {e}")

def mark_needs_continuation(
    task_id: str,
    reason: str,
    branch_name: str | None = None,
    agent_name: str | None = None,
) -> dict:
    """Mark a task as needing continuation."""
    sdk = get_sdk()
    return sdk.tasks.update(
        task_id,
        queue="needs_continuation",
        reason=reason,
        branch_name=branch_name,
        agent_name=agent_name,
    )

def resume_task(task_id: str, agent_name: str | None = None) -> dict:
    """Resume a held or continuation task."""
    sdk = get_sdk()
    orchestrator_id = get_orchestrator_id()
    return sdk.tasks.update(
        task_id,
        queue="claimed",
        claimed_by=agent_name or "unknown",
        orchestrator_id=orchestrator_id,
    )

def find_task_by_id(task_id: str, queues: list[str] | None = None) -> dict[str, Any] | None:
    """Find a task by ID, optionally filtered by queue state."""
    task = get_task_by_id(task_id)

    if task is None:
        return None

    if queues is not None:
        task_queue = task.get("queue")
        if task_queue not in queues:
            return None

    file_path_str = task.get("file_path")
    if file_path_str and "content" not in task:
        try:
            tasks_dir = get_tasks_file_dir()
            task_file = tasks_dir / Path(file_path_str).name
            if task_file.exists():
                task["file_path"] = str(task_file)
                task["content"] = task_file.read_text()
        except (FileNotFoundError, IOError):
            pass  # Task file missing — content stays empty

    return task

def get_continuation_tasks(agent_name: str | None = None) -> list[dict[str, Any]]:
    """Get tasks that need continuation, optionally filtered by agent."""
    tasks = list_tasks("needs_continuation")

    if agent_name:
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
    acceptance_criteria: list[str] | str,
    priority: str = "P1",
    branch: str | None = None,
    flow: str | None = None,
    created_by: str = "human",
    blocked_by: str | None = None,
    project_id: str | None = None,
    queue: str = "incoming",
    checks: list[str] | None = None,
    breakdown_depth: int = 0,
) -> Path:
    """Create a new task file in the specified queue."""
    if not branch:
        if project_id:
            try:
                sdk = get_sdk()
                project = sdk.projects.get(project_id)
                if project and project.get("branch"):
                    branch = project["branch"]
            except Exception as e:
                print(f"Warning: Failed to fetch project {project_id} for branch: {e}")
        if not branch:
            branch = get_base_branch()
    task_id = uuid4().hex[:8]
    filename = f"TASK-{task_id}.md"

    if not blocked_by or blocked_by == "None":
        blocked_by = None

    if isinstance(acceptance_criteria, str):
        acceptance_criteria = [
            line for line in acceptance_criteria.splitlines() if line.strip()
        ]

    criteria_lines = []
    for c in acceptance_criteria:
        stripped = c.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
            criteria_lines.append(stripped)
        else:
            criteria_lines.append(f"- [ ] {stripped}")
    criteria_md = "\n".join(criteria_lines)

    blocked_by_line = f"BLOCKED_BY: {blocked_by}\n" if blocked_by else ""
    project_line = f"PROJECT: {project_id}\n" if project_id else ""
    checks_line = f"CHECKS: {','.join(checks)}\n" if checks else ""
    breakdown_depth_line = f"BREAKDOWN_DEPTH: {breakdown_depth}\n" if breakdown_depth > 0 else ""

    task_path = get_tasks_file_dir() / filename

    content = f"""# [TASK-{task_id}] {title}

ROLE: {role}
PRIORITY: {priority}
BRANCH: {branch}
CREATED: {datetime.now().isoformat()}
CREATED_BY: {created_by}
{project_line}{blocked_by_line}{checks_line}{breakdown_depth_line}
## Context
{context}

## Acceptance Criteria
{criteria_md}
"""

    task_path.write_text(content)

    hooks_json = None
    try:
        from .hook_manager import HookManager
        hm = HookManager(sdk=get_sdk())
        hooks_list = hm.resolve_hooks_for_task(task_type=None)
        if hooks_list:
            import json as _json
            hooks_json = _json.dumps(hooks_list)
    except Exception as e:
        print(f"Warning: Failed to resolve hooks: {e}", file=sys.stderr)

    try:
        sdk = get_sdk()
        create_kwargs: dict[str, Any] = dict(
            id=task_id,
            file_path=filename,
            title=title,
            role=role,
            priority=priority,
            queue=queue,
            branch=branch,
            hooks=hooks_json,
            flow=flow if flow is not None else ("project" if project_id else "default"),
            metadata={
                "created_by": created_by,
                "project_id": project_id,
                "checks": checks,
                "breakdown_depth": breakdown_depth,
            },
        )
        # blocked_by must be a top-level field, not in metadata —
        # the server uses it to prevent claiming blocked tasks.
        if blocked_by:
            create_kwargs["blocked_by"] = blocked_by
        sdk.tasks.create(**create_kwargs)
    except Exception as e:
        print(f"Warning: Failed to register task with API: {e}", file=sys.stderr)

    logger = get_task_logger(task_id)
    logger.log_created(
        created_by=created_by,
        priority=priority,
        role=role,
        queue=queue,
    )

    return task_path

def is_task_still_valid(task_id: str) -> bool:
    """Check if a task still exists in active queues."""
    task = find_task_by_id(task_id, queues=ACTIVE_QUEUES)
    return task is not None

def get_task_by_id(task_id: str) -> dict[str, Any] | None:
    """Get a task by ID from the API."""
    try:
        sdk = get_sdk()
        return sdk.tasks.get(task_id)
    except Exception as e:
        print(f"Warning: Failed to get task {task_id}: {e}")
        return None

def list_tasks(subdir: str) -> list[dict[str, Any]]:
    """List all tasks in a queue."""
    try:
        sdk = get_sdk()
        tasks = sdk.tasks.list(queue=subdir)

        priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        tasks.sort(key=lambda t: (
            0 if t.get("expedite") else 1,  # Expedited tasks first
            priority_order.get(t.get("priority", "P2"), 2),
            t.get("created_at") or t.get("created") or "",
        ))
        return tasks
    except Exception as e:
        print(f"Warning: Failed to list tasks in queue {subdir}: {e}")
        return []

def approve_and_merge(
    task_id: str,
    merge_method: str = "merge",
) -> dict[str, Any]:
    """Approve a task and merge its PR via BEFORE_MERGE hooks."""
    from .hooks import HookContext, HookPoint, HookStatus, run_hooks
    from .task_notes import cleanup_task_notes
    from .task_thread import cleanup_thread

    sdk = get_sdk()
    task = sdk.tasks.get(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}

    pr_number = task.get("pr_number")
    pr_url = task.get("pr_url")

    result: dict[str, Any] = {"task_id": task_id, "merged": False, "pr_url": pr_url}

    ctx = HookContext(
        task_id=task_id,
        task_title=task.get("title", ""),
        task_path=task.get("file_path", ""),
        task_type=task.get("type"),
        branch_name=task.get("branch_name", ""),
        base_branch=task.get("base_branch", "main"),
        worktree=Path(task.get("file_path", "")).parent,
        agent_name=task.get("assigned_to", ""),
        extra={
            "pr_number": pr_number,
            "pr_url": pr_url,
            "merge_method": merge_method,
        },
    )

    all_ok, hook_results = run_hooks(HookPoint.BEFORE_MERGE, ctx)

    if not all_ok:
        last = hook_results[-1] if hook_results else None
        error_msg = last.message if last else "BEFORE_MERGE hooks failed"
        result["error"] = error_msg
        return result

    for hr in hook_results:
        if hr.status == HookStatus.SUCCESS and hr.context.get("pr_number"):
            result["merged"] = True
            break

    sdk.tasks.accept(task_id, accepted_by="scheduler")

    cleanup_task_notes(task_id)
    cleanup_thread(task_id)

    return result
