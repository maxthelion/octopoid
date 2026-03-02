"""Task lifecycle, CRUD, and query operations.

All task state transitions use the Octopoid SDK API.
The server is the single source of truth for task content.
"""

import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import (
    ACTIVE_QUEUES,
    get_base_branch,
    get_scope,
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

    # 1-hour lease (agents take 5-30 min). The scheduler's renew_active_leases()
    # job extends this automatically for tasks with live agent processes.
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

def request_intervention(
    task_id: str,
    reason: str,
    source: str,
    previous_queue: str,
    **sdk_kwargs,
) -> dict:
    """Flag a task for intervention without moving it to a different queue.

    Sets needs_intervention=True on the task and posts a message to the fixer
    actor with the intervention context. The task stays in its current queue.

    Called by fail_task() when a task first encounters an error. Records the
    intervention context (previous queue, error info, step progress) both in a
    message (primary — fixer reads this) and in intervention_context.json
    (fallback for prompt rendering).

    Args:
        task_id: Task identifier
        reason: Human-readable reason for intervention
        source: Categorisation tag for the error origin
        previous_queue: Queue the task was in when the error occurred (used to
            resume the correct flow transition after a fix)
        **sdk_kwargs: Additional fields passed to sdk.tasks.update

    Returns:
        Updated task dict from the server.
    """
    import json as _json

    sdk = get_sdk()
    reason_truncated = reason[:500] + ("..." if len(reason) > 500 else "")

    # Read step progress if execute_steps wrote it
    from .config import get_tasks_dir
    task_dir = get_tasks_dir() / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    steps_completed: list[str] = []
    step_that_failed = ""
    progress_file = task_dir / "step_progress.json"
    if progress_file.exists():
        try:
            progress = _json.loads(progress_file.read_text())
            steps_completed = progress.get("completed") or []
            step_that_failed = progress.get("failed") or ""
        except Exception:
            pass

    intervention_context = {
        "previous_queue": previous_queue,
        "error_source": source,
        "error_message": reason_truncated,
        "steps_completed": steps_completed,
        "step_that_failed": step_that_failed,
        "entered_at": datetime.now().isoformat(),
    }

    # Persist intervention context in task dir (fallback for fixer prompt rendering)
    try:
        (task_dir / "intervention_context.json").write_text(
            _json.dumps(intervention_context, indent=2)
        )
    except OSError as write_err:
        print(f"[{datetime.now().isoformat()}] WARN: Failed to write intervention_context for {task_id}: {write_err}")

    # Set needs_intervention=True and move to the requires-intervention queue
    # so the fixer agent can claim and process it.
    result = sdk.tasks.update(
        task_id,
        queue="requires-intervention",
        needs_intervention=True,
        execution_notes=f"needs-intervention: {reason_truncated}",
        **sdk_kwargs,
    )

    try:
        logger = get_task_logger(task_id)
        logger.log_requeued(
            from_queue=previous_queue,
            to_queue="needs-intervention",
            reason=reason_truncated,
        )
    except Exception as log_err:
        print(f"[{datetime.now().isoformat()}] WARN: task log write failed for {task_id}: {log_err}")

    # Post structured message to fixer actor (primary intervention context delivery)
    steps_str = ", ".join(steps_completed) if steps_completed else "none"
    ctx_json = _json.dumps(intervention_context, indent=2)
    try:
        sdk.messages.create(
            task_id=task_id,
            from_actor="scheduler",
            to_actor="fixer",
            type="intervention_request",
            content=(
                f"## Intervention Request\n\n"
                f"**Error source:** {source}\n"
                f"**Error:** {reason_truncated}\n"
                f"**Previous queue:** {previous_queue}\n"
                f"**Steps completed:** {steps_str}\n"
                f"**Step that failed:** {step_that_failed or 'unknown'}\n\n"
                f"A fixer agent will diagnose and fix this automatically.\n\n"
                f"```json\n{ctx_json}\n```"
            ),
        )
    except Exception as msg_err:
        print(f"[{datetime.now().isoformat()}] WARN: Failed to post intervention message for {task_id}: {msg_err}")

    print(
        f"[{datetime.now().isoformat()}] INTERVENTION task={task_id} "
        f"source={source} previous_queue={previous_queue} reason={reason_truncated[:200]}"
    )
    return result


def fail_task(task_id: str, reason: str, source: str, **sdk_kwargs) -> dict:
    """Route a failing task — sets needs_intervention (first failure) or to failed (terminal).

    On first failure (task does not already have needs_intervention=True): sets
    needs_intervention=True and posts an intervention_request message so the
    fixer agent can diagnose and resume. The task stays in its current queue.

    On second failure (task already has needs_intervention=True, meaning the
    fixer itself failed): clears the flag and moves to the true 'failed'
    terminal state.

    Raises ValueError if the task is already in the 'done' queue — done is a
    terminal success state and must not be overwritten by a failure.

    Args:
        task_id: Task identifier
        reason: Human-readable reason for failure
        source: Categorisation tag for the failure origin, e.g.:
            "flow-dispatch-error", "step-failure-circuit-breaker",
            "lease-expiry-circuit-breaker", "spawn-failure-circuit-breaker",
            "guard-empty-description"
        **sdk_kwargs: Additional fields passed to sdk.tasks.update
            (e.g. claimed_by=None, lease_expires_at=None, attempt_count=N)

    Returns:
        Updated task dict from the server.
    """
    sdk = get_sdk()
    reason_truncated = reason[:500] + ("..." if len(reason) > 500 else "")
    reason_stdout = reason[:200] + ("..." if len(reason) > 200 else "")

    # Guard: never overwrite the done queue with failed.
    current_task = sdk.tasks.get(task_id)
    current_queue = (current_task or {}).get("queue")
    if current_queue == "done":
        raise ValueError(
            f"fail_task: refusing to move task {task_id} from 'done' to 'failed' "
            f"(source={source}, reason={reason_stdout})"
        )

    # If needs_intervention is already set, the fixer also failed → true terminal failure.
    if (current_task or {}).get("needs_intervention"):
        result = sdk.tasks.update(
            task_id, queue="failed", needs_intervention=False,
            execution_notes=reason_truncated, **sdk_kwargs
        )
        try:
            logger = get_task_logger(task_id)
            logger.log_failed(error=reason_truncated, source=source)
        except Exception as log_err:
            print(f"[{datetime.now().isoformat()}] WARN: task log write failed for {task_id}: {log_err}")
        print(f"[{datetime.now().isoformat()}] FAILED task={task_id} source={source} reason={reason_stdout}")
        return result

    # First failure: set needs_intervention=True (task stays in current queue).
    # Ensure previous_queue is a plain string (current_queue may be None).
    previous_queue = current_queue if isinstance(current_queue, str) else "unknown"
    return request_intervention(
        task_id,
        reason=reason,
        source=source,
        previous_queue=previous_queue,
        **sdk_kwargs,
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

@dataclass
class TaskSpec:
    """All inputs needed to create a task."""

    title: str
    role: str
    context: str
    acceptance_criteria: list[str] | str
    priority: str = "P1"
    branch: str | None = None
    flow: str | None = None
    created_by: str = "human"
    blocked_by: str | None = None
    project_id: str | None = None
    queue: str = "incoming"
    checks: list[str] | None = None
    breakdown_depth: int = 0


def _resolve_branch(spec: TaskSpec) -> str:
    """Resolve branch: use spec.branch, fetch from project, or fall back to base."""
    if spec.branch:
        return spec.branch
    if spec.project_id:
        try:
            sdk = get_sdk()
            project = sdk.projects.get(spec.project_id)
            if project and project.get("branch"):
                return project["branch"]
            elif project and not project.get("branch"):
                print(
                    f"WARNING: Project {spec.project_id} has no branch set. "
                    f"Task will fall back to base branch.",
                    file=sys.stderr,
                )
        except Exception as e:
            print(f"Warning: Failed to fetch project {spec.project_id} for branch: {e}")
    return get_base_branch()


def _normalize_criteria(criteria: list[str] | str) -> list[str]:
    """Return criteria as a list of '- [ ] ...' checkbox lines."""
    if isinstance(criteria, str):
        criteria = [line for line in criteria.splitlines() if line.strip()]
    result = []
    for c in criteria:
        stripped = c.strip()
        result.append(
            stripped if stripped.startswith(("- [ ]", "- [x]")) else f"- [ ] {stripped}"
        )
    return result


def _build_task_content(
    spec: TaskSpec, task_id: str, branch: str, criteria: list[str]
) -> str:
    """Build the markdown content for a task."""
    blocked_by_line = f"BLOCKED_BY: {spec.blocked_by}\n" if spec.blocked_by else ""
    project_line = f"PROJECT: {spec.project_id}\n" if spec.project_id else ""
    checks_line = f"CHECKS: {','.join(spec.checks)}\n" if spec.checks else ""
    breakdown_depth_line = (
        f"BREAKDOWN_DEPTH: {spec.breakdown_depth}\n" if spec.breakdown_depth > 0 else ""
    )
    criteria_md = "\n".join(criteria)
    return f"""# [TASK-{task_id}] {spec.title}

ROLE: {spec.role}
PRIORITY: {spec.priority}
BRANCH: {branch}
CREATED: {datetime.now().isoformat()}
CREATED_BY: {spec.created_by}
{project_line}{blocked_by_line}{checks_line}{breakdown_depth_line}
## Context
{spec.context}

## Acceptance Criteria
{criteria_md}
"""


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
) -> str:
    """Create a new task and register it on the server with full content.

    Returns the bare task ID (e.g. "47766b7e"), not "TASK-47766b7e".
    Callers that need the filename can construct it themselves (e.g. f"TASK-{task_id}.md").
    """
    if not blocked_by or blocked_by == "None":
        blocked_by = None

    spec = TaskSpec(
        title=title,
        role=role,
        context=context,
        acceptance_criteria=acceptance_criteria,
        priority=priority,
        branch=branch,
        flow=flow,
        created_by=created_by,
        blocked_by=blocked_by,
        project_id=project_id,
        queue=queue,
        checks=checks,
        breakdown_depth=breakdown_depth,
    )

    resolved_branch = _resolve_branch(spec)
    task_id = uuid4().hex[:8]
    filename = f"TASK-{task_id}.md"
    criteria = _normalize_criteria(spec.acceptance_criteria)
    content = _build_task_content(spec, task_id, resolved_branch, criteria)

    try:
        sdk = get_sdk()
        create_kwargs: dict[str, Any] = dict(
            id=task_id,
            file_path=filename,
            title=title,
            role=role,
            priority=priority,
            queue=queue,
            branch=resolved_branch,
            content=content,
            flow=flow if flow is not None else ("project" if project_id else "default"),
            metadata={
                "created_by": created_by,
                "checks": checks,
                "breakdown_depth": breakdown_depth,
            },
        )
        if project_id:
            create_kwargs["project_id"] = project_id
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

    return task_id

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
    """List all tasks in a queue, filtered to the current scope."""
    try:
        sdk = get_sdk()
        tasks = sdk.tasks.list(queue=subdir)

        # Filter by scope as a client-side safety net.
        # The SDK sends scope as a query param, but if the server does not filter
        # by it (e.g. older server version), tasks from other scopes would leak
        # into queue counts and status displays, blocking capacity checks.
        scope = get_scope()
        if scope:
            tasks = [t for t in tasks if t.get("scope") == scope]

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

def cancel_task(task_id: str) -> dict[str, Any]:
    """Cancel a task with full cleanup: kill agent, remove worktree and runtime, delete server record.

    Handles partial state gracefully — each step is attempted independently so
    that a missing PID, worktree, or server record does not prevent the others
    from being cleaned up.

    Steps:
    1. Find and kill the agent process (searches all blueprint running_pids.json)
    2. Remove the git worktree cleanly (git worktree remove --force)
    3. Remove the runtime directory (.octopoid/runtime/tasks/<id>/)
    4. Delete the task on the server via SDK

    Args:
        task_id: Task identifier (short hex ID, e.g. "a7517c0d")

    Returns:
        Dict with keys: task_id, killed_pid, worktree_removed, runtime_removed, server_deleted, errors
    """
    import os
    import shutil
    import signal

    from .config import find_parent_project, get_agents_runtime_dir, get_tasks_dir
    from .git_utils import _remove_worktree
    from .pool import load_blueprint_pids, save_blueprint_pids

    result: dict[str, Any] = {
        "task_id": task_id,
        "killed_pid": None,
        "worktree_removed": False,
        "runtime_removed": False,
        "server_deleted": False,
        "errors": [],
    }

    # -------------------------------------------------------------------------
    # Step 1: Kill agent process
    # -------------------------------------------------------------------------
    agents_dir = get_agents_runtime_dir()
    if agents_dir.exists():
        for agent_dir in agents_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            blueprint_name = agent_dir.name
            pids = load_blueprint_pids(blueprint_name)
            for pid, info in list(pids.items()):
                if info.get("task_id") == task_id:
                    # Try to kill the process group (SIGTERM first, then SIGKILL)
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, OSError):
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except (ProcessLookupError, PermissionError, OSError):
                            pass  # Already dead
                    result["killed_pid"] = pid
                    # Remove from PID tracking so the scheduler doesn't try to
                    # process a result for this cancelled task
                    del pids[pid]
                    try:
                        save_blueprint_pids(blueprint_name, pids)
                    except Exception as e:
                        result["errors"].append(f"pid_tracking: {e}")
                    break
            if result["killed_pid"] is not None:
                break

    # -------------------------------------------------------------------------
    # Step 2: Remove git worktree
    # -------------------------------------------------------------------------
    worktree_path = get_tasks_dir() / task_id / "worktree"
    if worktree_path.exists():
        try:
            parent_repo = find_parent_project()
            _remove_worktree(parent_repo, worktree_path)
            result["worktree_removed"] = True
        except Exception as e:
            result["errors"].append(f"worktree_remove: {e}")
            # If git worktree remove failed, try manual rmtree as fallback
            try:
                shutil.rmtree(worktree_path)
                result["worktree_removed"] = True
            except Exception as e2:
                result["errors"].append(f"worktree_rmtree: {e2}")
    else:
        result["worktree_removed"] = True  # Nothing to remove

    # -------------------------------------------------------------------------
    # Step 3: Remove runtime directory
    # -------------------------------------------------------------------------
    task_dir = get_tasks_dir() / task_id
    if task_dir.exists():
        try:
            shutil.rmtree(task_dir)
            result["runtime_removed"] = True
        except Exception as e:
            result["errors"].append(f"runtime_rmtree: {e}")
    else:
        result["runtime_removed"] = True  # Nothing to remove

    # -------------------------------------------------------------------------
    # Step 4: Delete task on server
    # -------------------------------------------------------------------------
    try:
        sdk = get_sdk()
        sdk.tasks.delete(task_id)
        result["server_deleted"] = True
    except Exception as e:
        err_str = str(e)
        # Treat 404 as success — task already gone from server
        if "404" in err_str or "not found" in err_str.lower():
            result["server_deleted"] = True
        else:
            result["errors"].append(f"server_delete: {e}")

    return result


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
