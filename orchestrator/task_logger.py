"""Per-task logging infrastructure for tracking task lifecycle events.

Provides a TaskLogger class that creates and maintains per-task log files
in .orchestrator/logs/tasks/ to track all state transitions and events
across the entire task lifecycle.

Each task gets a persistent log file (TASK-<id>.log) that survives task
completion and provides an audit trail for debugging.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import get_orchestrator_dir


def get_task_log_dir() -> Path:
    """Get the task logs directory, creating it if needed.

    Returns:
        Path to .orchestrator/logs/tasks/
    """
    log_dir = get_orchestrator_dir() / "logs" / "tasks"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_task_log_path(task_id: str) -> Path:
    """Get the path to a task's log file.

    Args:
        task_id: Task identifier (e.g. "9f5cda4b")

    Returns:
        Path to TASK-<id>.log
    """
    log_dir = get_task_log_dir()
    return log_dir / f"TASK-{task_id}.log"


class TaskLogger:
    """Logger for tracking task lifecycle events.

    Each task gets a persistent log file that tracks all state transitions:
    - CREATED: Task creation
    - CLAIMED: Task claimed by an agent (with attempt number)
    - SUBMITTED: Task submitted for review
    - ACCEPTED: Task accepted and moved to done
    - REJECTED: Task rejected and returned to incoming
    - FAILED: Task failed with error

    Log entries are timestamped and include relevant metadata for debugging.
    """

    def __init__(self, task_id: str):
        """Initialize a task logger.

        Args:
            task_id: Task identifier (e.g. "9f5cda4b")
        """
        self.task_id = task_id
        self.log_path = get_task_log_path(task_id)

    def _write_entry(self, event: str, **kwargs: Any) -> None:
        """Write a log entry to the task log file.

        Args:
            event: Event type (CREATED, CLAIMED, SUBMITTED, etc.)
            **kwargs: Additional metadata to include in the log entry
        """
        timestamp = datetime.now().isoformat()

        # Build metadata string
        metadata_parts = []
        for key, value in kwargs.items():
            if value is not None:
                metadata_parts.append(f"{key}={value}")
        metadata = " ".join(metadata_parts)

        # Format: [timestamp] EVENT metadata
        entry = f"[{timestamp}] {event}"
        if metadata:
            entry += f" {metadata}"
        entry += "\n"

        # Append to log file (create if doesn't exist)
        with open(self.log_path, "a") as f:
            f.write(entry)

    def log_created(
        self,
        created_by: str = "human",
        priority: str = "P2",
        role: str | None = None,
        queue: str = "incoming",
    ) -> None:
        """Log task creation.

        Args:
            created_by: Who created the task
            priority: Task priority (P0, P1, P2)
            role: Target role (implement, test, review, etc.)
            queue: Initial queue (default: incoming)
        """
        self._write_entry(
            "CREATED",
            by=created_by,
            priority=priority,
            role=role,
            queue=queue,
        )

    def log_claimed(
        self,
        claimed_by: str,
        attempt: int = 1,
        from_queue: str = "incoming",
    ) -> None:
        """Log task claim.

        Args:
            claimed_by: Agent name that claimed the task
            attempt: Claim attempt number (1-indexed)
            from_queue: Queue task was claimed from
        """
        self._write_entry(
            "CLAIMED",
            by=claimed_by,
            attempt=attempt,
            from_queue=from_queue,
        )

    def log_submitted(
        self,
        commits: int = 0,
        turns: int | None = None,
    ) -> None:
        """Log task submission.

        Args:
            commits: Number of commits made
            turns: Number of Claude turns used (optional)
        """
        self._write_entry(
            "SUBMITTED",
            commits=commits,
            turns=turns,
        )

    def log_accepted(
        self,
        accepted_by: str | None = None,
    ) -> None:
        """Log task acceptance.

        Args:
            accepted_by: Who/what accepted the task (gatekeeper, pre-check, human, etc.)
        """
        self._write_entry(
            "ACCEPTED",
            by=accepted_by,
        )

    def log_rejected(
        self,
        reason: str,
        rejected_by: str | None = None,
    ) -> None:
        """Log task rejection.

        Args:
            reason: Rejection reason (truncated if too long)
            rejected_by: Who/what rejected the task
        """
        # Truncate reason to keep log entries readable
        reason_summary = reason[:100] + "..." if len(reason) > 100 else reason
        # Replace newlines with spaces for single-line logging
        reason_summary = reason_summary.replace("\n", " ")

        self._write_entry(
            "REJECTED",
            reason=f'"{reason_summary}"',
            by=rejected_by,
        )

    def log_failed(
        self,
        error: str,
    ) -> None:
        """Log task failure.

        Args:
            error: Error message (truncated if too long)
        """
        # Truncate error to keep log entries readable
        error_summary = error[:100] + "..." if len(error) > 100 else error
        # Replace newlines with spaces for single-line logging
        error_summary = error_summary.replace("\n", " ")

        self._write_entry(
            "FAILED",
            error=f'"{error_summary}"',
        )


def parse_task_log(task_id: str) -> list[dict[str, Any]]:
    """Parse a task's log file into structured events.

    Args:
        task_id: Task identifier

    Returns:
        List of event dictionaries with 'timestamp', 'event', and metadata
    """
    log_path = get_task_log_path(task_id)

    if not log_path.exists():
        return []

    events = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # Parse format: [timestamp] EVENT metadata
            if not line.startswith("["):
                continue

            # Extract timestamp
            timestamp_end = line.find("]")
            if timestamp_end == -1:
                continue

            timestamp = line[1:timestamp_end]
            rest = line[timestamp_end + 1:].strip()

            # Extract event type and metadata
            parts = rest.split(maxsplit=1)
            if not parts:
                continue

            event_type = parts[0]
            metadata_str = parts[1] if len(parts) > 1 else ""

            # Parse metadata (simple key=value pairs)
            metadata = {"timestamp": timestamp, "event": event_type}

            # Parse key=value pairs
            import re
            # Handle quoted values (e.g., reason="...") and simple key=value
            for match in re.finditer(r'(\w+)=(".*?"|[^\s]+)', metadata_str):
                key = match.group(1)
                value = match.group(2)
                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                metadata[key] = value

            events.append(metadata)

    return events


def get_claim_count(task_id: str) -> int:
    """Get the number of times a task has been claimed.

    Args:
        task_id: Task identifier

    Returns:
        Number of CLAIMED events in the task log
    """
    events = parse_task_log(task_id)
    return sum(1 for event in events if event["event"] == "CLAIMED")


def get_first_claim_time(task_id: str) -> datetime | None:
    """Get the timestamp of the first claim.

    Args:
        task_id: Task identifier

    Returns:
        Datetime of first CLAIMED event, or None if never claimed
    """
    events = parse_task_log(task_id)
    claimed_events = [e for e in events if e["event"] == "CLAIMED"]

    if not claimed_events:
        return None

    try:
        return datetime.fromisoformat(claimed_events[0]["timestamp"])
    except (KeyError, ValueError):
        return None


def get_last_claim_time(task_id: str) -> datetime | None:
    """Get the timestamp of the most recent claim.

    Args:
        task_id: Task identifier

    Returns:
        Datetime of last CLAIMED event, or None if never claimed
    """
    events = parse_task_log(task_id)
    claimed_events = [e for e in events if e["event"] == "CLAIMED"]

    if not claimed_events:
        return None

    try:
        return datetime.fromisoformat(claimed_events[-1]["timestamp"])
    except (KeyError, ValueError):
        return None
