"""Per-task logging infrastructure for tracking task lifecycle.

This module provides persistent logging for individual tasks, tracking all
state transitions from creation through completion. Each task gets its own
log file in .orchestrator/logs/tasks/ that survives task completion.

Example log format:
    [2026-02-11T10:07:46] CREATED by=human priority=P2 role=orchestrator_impl
    [2026-02-11T10:16:17] CLAIMED by=orch-impl-1 attempt=1
    [2026-02-11T10:28:52] SUBMITTED commits=1 turns=125
    [2026-02-11T10:29:15] REJECTED reason="merge conflicts" rejected_by=pre-check
    [2026-02-11T10:47:05] CLAIMED by=orch-impl-1 attempt=2
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from .config import get_orchestrator_dir


def get_task_logs_dir() -> Path:
    """Get the task logs directory, creating it if needed.

    Returns:
        Path to .orchestrator/logs/tasks/
    """
    logs_dir = get_orchestrator_dir() / "logs" / "tasks"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_task_log_path(task_id: str) -> Path:
    """Get the log file path for a specific task.

    Args:
        task_id: Task identifier (e.g. "9f5cda4b")

    Returns:
        Path to TASK-<id>.log
    """
    logs_dir = get_task_logs_dir()
    return logs_dir / f"TASK-{task_id}.log"


def _write_log_entry(task_id: str, event: str, **kwargs: Any) -> None:
    """Write a timestamped log entry for a task.

    Args:
        task_id: Task identifier
        event: Event type (CREATED, CLAIMED, SUBMITTED, etc.)
        **kwargs: Additional key=value pairs to log
    """
    timestamp = datetime.now().isoformat(timespec='seconds')
    log_path = get_task_log_path(task_id)

    # Format: [timestamp] EVENT key1=value1 key2=value2
    parts = [f"[{timestamp}]", event]
    for key, value in sorted(kwargs.items()):
        # Quote values that contain spaces
        if value is None:
            continue
        value_str = str(value)
        if ' ' in value_str or '=' in value_str:
            parts.append(f'{key}="{value_str}"')
        else:
            parts.append(f'{key}={value_str}')

    log_line = ' '.join(parts) + '\n'

    # Append to log file (create if doesn't exist)
    with open(log_path, 'a') as f:
        f.write(log_line)


def log_created(
    task_id: str,
    created_by: str,
    priority: str,
    role: str,
    source: str | None = None
) -> None:
    """Log task creation.

    Args:
        task_id: Task identifier
        created_by: Creator (e.g. "human", "github-issue-monitor", "product-manager")
        priority: Priority level (P0, P1, P2, P3)
        role: Role that will handle the task
        source: Optional source identifier (e.g. GitHub issue number)
    """
    _write_log_entry(
        task_id,
        "CREATED",
        by=created_by,
        priority=priority,
        role=role,
        source=source
    )


def log_claimed(
    task_id: str,
    agent_name: str,
    attempt: int
) -> None:
    """Log task being claimed by an agent.

    Args:
        task_id: Task identifier
        agent_name: Name of agent claiming the task
        attempt: Claim attempt number (1-indexed)
    """
    _write_log_entry(
        task_id,
        "CLAIMED",
        by=agent_name,
        attempt=attempt
    )


def log_submitted(
    task_id: str,
    commits: int,
    turns: int | None = None
) -> None:
    """Log task submission for review.

    Args:
        task_id: Task identifier
        commits: Number of commits in submission
        turns: Optional turn count from agent
    """
    _write_log_entry(
        task_id,
        "SUBMITTED",
        commits=commits,
        turns=turns
    )


def log_rejected(
    task_id: str,
    reason: str,
    rejected_by: str
) -> None:
    """Log task rejection (sent back to queue).

    Args:
        task_id: Task identifier
        reason: Rejection reason
        rejected_by: Who rejected it (e.g. "pre-check", "reviewer", "validator")
    """
    _write_log_entry(
        task_id,
        "REJECTED",
        reason=reason,
        rejected_by=rejected_by
    )


def log_accepted(
    task_id: str,
    pr_number: int | None = None,
    reviewer: str | None = None
) -> None:
    """Log task acceptance and completion.

    Args:
        task_id: Task identifier
        pr_number: GitHub PR number if created
        reviewer: Who accepted it (optional)
    """
    _write_log_entry(
        task_id,
        "ACCEPTED",
        pr=pr_number,
        reviewer=reviewer
    )


def log_failed(
    task_id: str,
    reason: str,
    failed_by: str | None = None
) -> None:
    """Log task failure.

    Args:
        task_id: Task identifier
        reason: Failure reason
        failed_by: Who marked it failed (optional)
    """
    _write_log_entry(
        task_id,
        "FAILED",
        reason=reason,
        failed_by=failed_by
    )


def log_escalated(
    task_id: str,
    reason: str,
    escalated_by: str
) -> None:
    """Log task escalation.

    Args:
        task_id: Task identifier
        reason: Escalation reason
        escalated_by: Who escalated it
    """
    _write_log_entry(
        task_id,
        "ESCALATED",
        reason=reason,
        escalated_by=escalated_by
    )


def log_recycled(
    task_id: str,
    recycled_by: str,
    reason: str | None = None
) -> None:
    """Log task being recycled.

    Args:
        task_id: Task identifier
        recycled_by: Who recycled it
        reason: Optional recycling reason
    """
    _write_log_entry(
        task_id,
        "RECYCLED",
        recycled_by=recycled_by,
        reason=reason
    )


def get_claim_count(task_id: str) -> int:
    """Count how many times a task has been claimed.

    Reads the task log and counts CLAIMED entries.

    Args:
        task_id: Task identifier

    Returns:
        Number of times the task has been claimed (0 if never or no log)
    """
    log_path = get_task_log_path(task_id)
    if not log_path.exists():
        return 0

    count = 0
    with open(log_path, 'r') as f:
        for line in f:
            if ' CLAIMED ' in line:
                count += 1
    return count


def get_claim_times(task_id: str) -> tuple[datetime | None, datetime | None]:
    """Get first and last claim timestamps for a task.

    Args:
        task_id: Task identifier

    Returns:
        Tuple of (first_claim_time, last_claim_time) or (None, None) if never claimed
    """
    log_path = get_task_log_path(task_id)
    if not log_path.exists():
        return (None, None)

    first_claim = None
    last_claim = None

    with open(log_path, 'r') as f:
        for line in f:
            if ' CLAIMED ' in line:
                # Extract timestamp from [2026-02-11T10:16:17] format
                if line.startswith('[') and ']' in line:
                    timestamp_str = line[1:line.index(']')]
                    try:
                        timestamp = datetime.fromisoformat(timestamp_str)
                        if first_claim is None:
                            first_claim = timestamp
                        last_claim = timestamp
                    except ValueError:
                        pass  # Skip malformed timestamps

    return (first_claim, last_claim)
