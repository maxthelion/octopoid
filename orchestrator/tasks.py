"""Task lifecycle, CRUD, and query operations.

All task state transitions use the Octopoid SDK API.
Task files on disk are supplementary — the API is the source of truth.
"""

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import (
    ACTIVE_QUEUES,
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
    """Transition a task to a new queue with optional cleanup and logging.

    Args:
        task_id: Task identifier
        queue: Target queue name
        cleanup_worktree: Whether to clean up task worktree
        push_commits: Whether to push commits before cleanup
        log_fn: Optional function to call with task logger (e.g., lambda l: l.log_failed(...))
        **sdk_kwargs: Additional kwargs for SDK update call

    Returns:
        Updated task from API
    """
    sdk = get_sdk()
    result = sdk.tasks.update(task_id, queue=queue, **sdk_kwargs)

    if log_fn:
        log_fn(get_task_logger(task_id))

    if cleanup_worktree:
        from .git_utils import cleanup_task_worktree
        cleanup_task_worktree(task_id, push_commits=push_commits)

    return result

# =============================================================================
# Task Lifecycle Functions
# =============================================================================

def claim_task(
    role_filter: str | None = None,
    agent_name: str | None = None,
    from_queue: str = "incoming",
    type_filter: str | None = None,
) -> dict[str, Any] | None:
    """Atomically claim a task from the API server.

    The API server handles atomic claiming with lease-based coordination,
    preventing race conditions across distributed orchestrators.

    After claiming, reads the task file from disk to populate 'content',
    since the API stores the filename but not file content.

    Args:
        role_filter: Only claim tasks with this role (e.g., 'implement', 'test', 'breakdown')
        agent_name: Name of claiming agent (for logging in task)
        from_queue: Queue to claim from (default 'incoming')
        type_filter: Only claim tasks with this type (e.g., 'product', 'infrastructure')

    Returns:
        Task info dictionary (with 'content' from file) if claimed, None if no task

    Raises:
        FileNotFoundError: If the claimed task's file doesn't exist on disk
    """
    from .backpressure import get_queue_limits

    sdk = get_sdk()
    orchestrator_id = get_orchestrator_id()
    limits = get_queue_limits()

    # Claim via API (atomic operation with lease)
    # Server enforces max_claimed to prevent races between agents
    # Use 1-hour lease — agents typically take 5-30 minutes and there's
    # no lease renewal mechanism yet. The server default (5 min) is too short.
    task = sdk.tasks.claim(
        orchestrator_id=orchestrator_id,
        agent_name=agent_name or "unknown",
        role_filter=role_filter,
        type_filter=type_filter,
        max_claimed=limits.get("max_claimed"),
        lease_duration_seconds=3600,
    )

    if task is None:
        return None

    # Read task file content if available
    file_path_str = task.get("file_path")
    if file_path_str:
        # Try to read from tasks directory
        tasks_dir = get_tasks_file_dir()
        task_file = tasks_dir / Path(file_path_str).name
        if task_file.exists():
            task["file_path"] = str(task_file)
            task["content"] = task_file.read_text()

    # Log the claim
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
    """Submit a task for review via API (moves to provisional queue).

    The API server handles state transition to provisional queue.
    Auto-rejects 0-commit submissions from previously-claimed tasks.

    Args:
        task_id: Task identifier
        commits_count: Number of commits made during implementation
        turns_used: Number of Claude turns used

    Returns:
        Updated task from API or None if auto-rejected
    """
    sdk = get_sdk()

    # Get current task state from API
    task = sdk.tasks.get(task_id)
    if not task:
        print(f"Warning: Task {task_id} not found in API")
        return None

    # Auto-reject 0-commit submissions from previously-claimed tasks
    attempt_count = task.get("attempt_count", 0)
    rejection_count = task.get("rejection_count", 0)
    previously_claimed = attempt_count > 0 or rejection_count > 0

    if commits_count == 0 and previously_claimed:
        return reject_completion(
            task_id,
            reason="No commits made. Read the task file and rejection feedback, then implement the required changes.",
            accepted_by="submit_completion",
        )

    # Generate execution notes inline
    parts = []
    if commits_count > 0:
        parts.append(f"Created {commits_count} commit{'s' if commits_count != 1 else ''}")
    else:
        parts.append("No commits made")
    if turns_used:
        parts.append(f"{turns_used} turn{'s' if turns_used != 1 else ''} used")
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-n", str(min(commits_count, 5))],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            commit_summary = result.stdout.strip().replace("\n", "; ")
            parts.append(f"Changes: {commit_summary}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    execution_notes = ". ".join(parts) + "."

    # Submit via API (moves to provisional queue)
    result = sdk.tasks.submit(
        task_id=task_id,
        commits_count=commits_count,
        turns_used=turns_used or 0,
        execution_notes=execution_notes,
    )

    # Log the submission
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
    """Accept a provisional task via API (moves to done queue).

    Called by the pre-check or gatekeeper when a task passes.
    The API server handles state transition to done queue.

    Args:
        task_id: Task identifier
        accepted_by: Name of the agent or system that accepted (e.g. "scheduler", "gatekeeper", "human")

    Returns:
        Updated task from API
    """
    from .task_notes import cleanup_task_notes

    result = _transition(
        task_id,
        "done",
        cleanup_worktree=True,
        push_commits=True,
        log_fn=lambda l: l.log_accepted(accepted_by=accepted_by or "unknown")
    )

    cleanup_task_notes(task_id)
    return result

def reject_completion(
    task_id: str,
    reason: str,
    accepted_by: str | None = None,
) -> dict:
    """Reject a provisional task via API (moves back to incoming for retry).

    Called by the pre-check when a task fails (e.g., no commits).
    The API server increments attempt_count and moves to incoming queue.

    Args:
        task_id: Task identifier
        reason: Rejection reason
        accepted_by: Name of the agent or system that rejected

    Returns:
        Updated task from API
    """
    sdk = get_sdk()

    # Reject via API (moves to incoming queue, increments attempt_count)
    updated_task = sdk.tasks.reject(
        task_id=task_id,
        reason=reason,
        rejected_by=accepted_by
    )

    # Log the rejection
    logger = get_task_logger(task_id)
    logger.log_rejected(
        reason=reason,
        rejected_by=accepted_by or "unknown",
    )

    # Clean up ephemeral task worktree (worktree is deleted; next attempt gets fresh checkout)
    from .git_utils import cleanup_task_worktree
    cleanup_task_worktree(task_id, push_commits=True)

    return updated_task

def _insert_rejection_feedback(content: str, feedback_section: str) -> str:
    """Insert rejection feedback after metadata, before first ## heading."""
    # Strip existing rejection notices
    content = re.sub(r'\n*## Rejection Notice.*?(?=\n## |\Z)', '', content, flags=re.DOTALL)
    content = re.sub(r'\n*## Review Feedback \(rejection #\d+\).*?(?=\n## |\Z)', '', content, flags=re.DOTALL)
    # Find insertion point
    lines = content.split('\n')
    insert_idx = None
    for i, line in enumerate(lines):
        if line.startswith('## '):
            insert_idx = i
            break
    if insert_idx is not None:
        feedback_lines = feedback_section.rstrip('\n').split('\n')
        lines = lines[:insert_idx] + feedback_lines + ['', ''] + lines[insert_idx:]
    else:
        lines.append('')
        lines.extend(feedback_section.rstrip('\n').split('\n'))
    return '\n'.join(lines)

def review_reject_task(
    task_id: str,
    feedback: str,
    rejected_by: str | None = None,
    max_rejections: int = 3,
) -> tuple[str, str]:
    """Reject a provisional task with review feedback from gatekeepers.

    Increments rejection_count (distinct from attempt_count used by pre-check).
    If rejection_count reaches max_rejections, escalates to human attention
    instead of cycling back to the implementer.

    The task's branch is preserved so the implementer can push fixes.
    Rejection feedback is inserted near the top of the task file (after the
    metadata block, before ## Context) so agents see it immediately.

    Args:
        task_id: Task identifier
        feedback: Aggregated review feedback markdown
        rejected_by: Name of the reviewer/coordinator
        max_rejections: Maximum rejections before escalation (default 3)

    Returns:
        Tuple of (task_id, action) where action is 'rejected' or 'escalated'
    """
    # Get current rejection count from API
    rejection_count = 0
    try:
        sdk = get_sdk()
        api_task = sdk.tasks.get(task_id)
        if api_task:
            rejection_count = (api_task.get("rejection_count") or 0) + 1
    except Exception:
        pass

    escalated = rejection_count >= max_rejections

    # Insert feedback into the task file (file stays in place)
    tasks_dir = get_tasks_file_dir()
    task_file = tasks_dir / f"TASK-{task_id}.md"

    if task_file.exists():
        original_content = task_file.read_text()

        feedback_section = f"## Rejection Notice (rejection #{rejection_count})\n\n"
        feedback_section += "**WARNING: This task was previously attempted but the work was rejected.**\n"
        feedback_section += "**Existing code on the branch does NOT satisfy the acceptance criteria.**\n"
        feedback_section += "**You MUST make new commits to address the feedback below.**\n\n"
        feedback_section += f"{feedback}\n\n"
        feedback_section += f"REVIEW_REJECTED_AT: {datetime.now().isoformat()}\n"
        if rejected_by:
            feedback_section += f"REVIEW_REJECTED_BY: {rejected_by}\n"

        new_content = _insert_rejection_feedback(original_content, feedback_section)
        task_file.write_text(new_content)

    # Update API state — queue changes, file stays put
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

        # Log the review rejection
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
        from . import message_utils
        message_utils.warning(
            f"Task {task_id} escalated after {rejection_count} rejections",
            f"Task has been rejected {rejection_count} times by reviewers. "
            f"Human attention required.\n\nLatest feedback:\n{feedback[:1000]}",
            rejected_by or "gatekeeper",
            task_id,
        )

    action = "escalated" if escalated else "rejected"

    # Clean up ephemeral task worktree (task will be retried or escalated)
    from .git_utils import cleanup_task_worktree
    cleanup_task_worktree(task_id, push_commits=True)

    return (task_id, action)

def get_review_feedback(task_id: str) -> str | None:
    """Extract review feedback sections from a task's markdown file.

    Supports both the new '## Rejection Notice' format (inserted near top)
    and the legacy '## Review Feedback' format (appended at bottom).

    Args:
        task_id: Task identifier

    Returns:
        Combined feedback text or None if no feedback found
    """
    task = get_task_by_id(task_id)
    if not task:
        return None

    content = task.get("content", "")
    if not content:
        return None

    # Try new format first: ## Rejection Notice
    new_sections = re.findall(
        r'## Rejection Notice.*?\n(.*?)(?=\n## |\Z)',
        content,
        re.DOTALL,
    )

    if new_sections:
        return "\n\n---\n\n".join(section.strip() for section in new_sections)

    # Fall back to legacy format: ## Review Feedback
    legacy_sections = re.findall(
        r'## Review Feedback \(rejection #\d+\)\s*\n(.*?)(?=\n## |\Z)',
        content,
        re.DOTALL,
    )

    if not legacy_sections:
        return None

    return "\n\n---\n\n".join(section.strip() for section in legacy_sections)

def escalate_to_planning(task_id: str, plan_id: str) -> dict:
    """Escalate a task to planning queue."""
    return _transition(task_id, "escalated")

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
    return _transition(task_id, "rejected")

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
    return _transition(task_id, "needs_continuation")

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

    # Filter by queue state (API is the source of truth)
    if queues is not None:
        task_queue = task.get("queue")
        if task_queue not in queues:
            return None

    # Read task file content if available
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
    acceptance_criteria: list[str] | str,
    priority: str = "P1",
    branch: str = "main",
    created_by: str = "human",
    blocked_by: str | None = None,
    project_id: str | None = None,
    queue: str = "incoming",
    checks: list[str] | None = None,
    breakdown_depth: int = 0,
) -> Path:
    """Create a new task file in the specified queue."""
    task_id = uuid4().hex[:8]
    filename = f"TASK-{task_id}.md"

    # Normalize blocked_by: ensure None/empty/string-"None" all become None
    if not blocked_by or blocked_by == "None":
        blocked_by = None

    # Normalize acceptance_criteria to a list of lines
    if isinstance(acceptance_criteria, str):
        acceptance_criteria = [
            line for line in acceptance_criteria.splitlines() if line.strip()
        ]

    # Build markdown checklist, preserving existing "- [ ]" prefixes
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

    # All task files go in one directory — API owns the queue state
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

    # Write local file
    task_path.write_text(content)

    # Resolve hooks from config for this task
    hooks_json = None
    try:
        from .hook_manager import HookManager
        hm = HookManager(sdk=get_sdk())
        hooks_list = hm.resolve_hooks_for_task(task_type=None)
        if hooks_list:
            import json as _json
            hooks_json = _json.dumps(hooks_list)
    except Exception as e:
        print(f"Warning: Failed to resolve hooks: {e}")

    # Register task with API server
    try:
        sdk = get_sdk()
        sdk.tasks.create(
            id=task_id,
            file_path=filename,
            title=title,
            role=role,
            priority=priority,
            context=context,
            acceptance_criteria="\n".join(criteria_lines),
            queue=queue,
            branch=branch,
            hooks=hooks_json,
            metadata={
                "created_by": created_by,
                "blocked_by": blocked_by,
                "project_id": project_id,
                "checks": checks,
                "breakdown_depth": breakdown_depth,
            }
        )
    except Exception as e:
        print(f"Warning: Failed to register task with API: {e}")
        # Still return task_path since local file was created
        # This allows offline task creation

    # Log the task creation
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
        task = sdk.tasks.get(task_id)
        return task
    except Exception as e:
        # Log error but don't crash
        print(f"Warning: Failed to get task {task_id}: {e}")
        return None

def list_tasks(subdir: str) -> list[dict[str, Any]]:
    """List all tasks in a queue."""
    try:
        sdk = get_sdk()
        tasks = sdk.tasks.list(queue=subdir)

        # Sort by: 1) expedite flag (expedited first), 2) priority (P0 first), 3) created time
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
    """Approve a task and merge its PR via BEFORE_MERGE hooks.

    Runs configured BEFORE_MERGE hooks (default: merge_pr) then accepts
    the task via the SDK.  If hooks fail, the task is NOT accepted.

    Args:
        task_id: Task identifier
        merge_method: Git merge method (merge, squash, rebase)

    Returns:
        Dict with result info (merged, pr_url, error)
    """
    from .hooks import HookContext, HookPoint, HookStatus, run_hooks
    from .task_notes import cleanup_task_notes

    sdk = get_sdk()
    task = sdk.tasks.get(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}

    pr_number = task.get("pr_number")
    pr_url = task.get("pr_url")

    result: dict[str, Any] = {"task_id": task_id, "merged": False, "pr_url": pr_url}

    # Build hook context
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

    # Run BEFORE_MERGE hooks (e.g. merge_pr)
    all_ok, hook_results = run_hooks(HookPoint.BEFORE_MERGE, ctx)

    if not all_ok:
        last = hook_results[-1] if hook_results else None
        error_msg = last.message if last else "BEFORE_MERGE hooks failed"
        result["error"] = error_msg
        return result

    # Check hook results for merge info
    for hr in hook_results:
        if hr.status == HookStatus.SUCCESS and hr.context.get("pr_number"):
            result["merged"] = True
            break

    # Accept the task via SDK
    sdk.tasks.accept(task_id, accepted_by="scheduler")

    cleanup_task_notes(task_id)

    return result
