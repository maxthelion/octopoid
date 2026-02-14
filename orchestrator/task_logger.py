"""Per-task logging for lifecycle events.

Creates persistent logs for each task that survive task completion,
tracking all state transitions across claims and submissions.

Log format:
    [ISO-timestamp] EVENT_TYPE field=value field=value ...

Example:
    [2026-02-14T10:30:45] CREATED by=human priority=P1 role=implement
    [2026-02-14T10:35:12] CLAIMED by=orch-impl-1 agent=orch-impl-1 attempt=1
    [2026-02-14T10:47:33] SUBMITTED commits=3 turns=42
    [2026-02-14T10:48:01] ACCEPTED accepted_by=auto-accept
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any


class TaskLogger:
    """Persistent logger for task lifecycle events.

    Each task gets its own log file at:
        .octopoid/logs/tasks/TASK-{id}.log

    The log persists across the entire task lifecycle and after completion,
    providing an authoritative audit trail.
    """

    def __init__(self, task_id: str, logs_dir: Path | None = None):
        """Initialize TaskLogger for a specific task.

        Args:
            task_id: Task identifier (with or without TASK- prefix)
            logs_dir: Override default logs directory (useful for testing)
        """
        # Normalize task ID (ensure TASK- prefix)
        if not task_id.startswith("TASK-"):
            task_id = f"TASK-{task_id}"

        self.task_id = task_id

        # Determine logs directory
        if logs_dir is None:
            from .config import get_orchestrator_dir
            logs_dir = get_orchestrator_dir() / "logs" / "tasks"

        self.logs_dir = logs_dir
        self.log_path = self.logs_dir / f"{task_id}.log"

        # Ensure directory exists
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _write_event(self, event: str, **fields: Any) -> None:
        """Write an event to the task log.

        Args:
            event: Event type (CREATED, CLAIMED, SUBMITTED, etc.)
            **fields: Key-value pairs to log
        """
        timestamp = datetime.now().isoformat(timespec="seconds")

        # Format fields as key=value pairs
        parts = [f"{k}={v}" for k, v in fields.items() if v is not None]
        fields_str = " ".join(parts)

        log_line = f"[{timestamp}] {event}"
        if fields_str:
            log_line += f" {fields_str}"
        log_line += "\n"

        # Append to log file
        with open(self.log_path, "a") as f:
            f.write(log_line)

    def log_created(
        self,
        created_by: str,
        priority: str,
        role: str | None = None,
        queue: str = "incoming",
        **extra: Any,
    ) -> None:
        """Log task creation.

        Args:
            created_by: Who/what created the task (e.g., "human", "github-issue-monitor")
            priority: Task priority (P0, P1, P2, P3)
            role: Task role (implement, breakdown, etc.)
            queue: Initial queue (default: incoming)
            **extra: Additional fields to log
        """
        self._write_event(
            "CREATED",
            by=created_by,
            priority=priority,
            role=role,
            queue=queue,
            **extra,
        )

    def log_claimed(
        self,
        claimed_by: str,
        agent: str,
        attempt: int,
        **extra: Any,
    ) -> None:
        """Log task claim.

        Args:
            claimed_by: Orchestrator ID that claimed the task
            agent: Agent name that will work on the task
            attempt: Claim attempt number (1 = first claim, 2+ = reclaim)
            **extra: Additional fields to log
        """
        self._write_event(
            "CLAIMED",
            by=claimed_by,
            agent=agent,
            attempt=attempt,
            **extra,
        )

    def log_submitted(
        self,
        commits: int,
        turns: int,
        **extra: Any,
    ) -> None:
        """Log task submission.

        Args:
            commits: Number of commits made
            turns: Number of agent turns used
            **extra: Additional fields to log (e.g., check_results)
        """
        self._write_event(
            "SUBMITTED",
            commits=commits,
            turns=turns,
            **extra,
        )

    def log_accepted(
        self,
        accepted_by: str,
        **extra: Any,
    ) -> None:
        """Log task acceptance.

        Args:
            accepted_by: Who accepted the task (e.g., "auto-accept", "human")
            **extra: Additional fields to log
        """
        self._write_event(
            "ACCEPTED",
            accepted_by=accepted_by,
            **extra,
        )

    def log_rejected(
        self,
        reason: str,
        rejected_by: str,
        **extra: Any,
    ) -> None:
        """Log task rejection.

        Args:
            reason: Why the task was rejected
            rejected_by: Who/what rejected the task
            **extra: Additional fields to log
        """
        self._write_event(
            "REJECTED",
            reason=reason,
            rejected_by=rejected_by,
            **extra,
        )

    def log_failed(
        self,
        error: str,
        **extra: Any,
    ) -> None:
        """Log task failure.

        Args:
            error: Error message or reason for failure
            **extra: Additional fields to log
        """
        self._write_event(
            "FAILED",
            error=error,
            **extra,
        )

    def log_requeued(
        self,
        from_queue: str,
        to_queue: str,
        reason: str | None = None,
        **extra: Any,
    ) -> None:
        """Log task being moved between queues.

        Args:
            from_queue: Source queue
            to_queue: Destination queue
            reason: Why the task was moved (optional)
            **extra: Additional fields to log
        """
        self._write_event(
            "REQUEUED",
            from_queue=from_queue,
            to_queue=to_queue,
            reason=reason,
            **extra,
        )

    def get_claim_count(self) -> int:
        """Count how many times this task has been claimed.

        Returns:
            Number of CLAIMED events in the log
        """
        if not self.log_path.exists():
            return 0

        try:
            with open(self.log_path) as f:
                return sum(1 for line in f if " CLAIMED " in line)
        except OSError:
            return 0

    def get_events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        """Parse and return log events.

        Args:
            event_type: Filter by event type (e.g., "CLAIMED"), or None for all

        Returns:
            List of event dicts with 'timestamp', 'event', and parsed fields
        """
        if not self.log_path.exists():
            return []

        events = []

        try:
            with open(self.log_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    # Parse: [timestamp] EVENT field=value field=value...
                    if not line.startswith("["):
                        continue

                    try:
                        # Extract timestamp
                        end_bracket = line.index("]")
                        timestamp = line[1:end_bracket]

                        # Extract rest
                        rest = line[end_bracket + 2:]  # Skip "] "
                        parts = rest.split(maxsplit=1)
                        if not parts:
                            continue

                        event = parts[0]

                        # Filter by event type if specified
                        if event_type and event != event_type:
                            continue

                        # Parse fields
                        fields = {"timestamp": timestamp, "event": event}
                        if len(parts) > 1:
                            for field_pair in parts[1].split():
                                if "=" in field_pair:
                                    key, value = field_pair.split("=", 1)
                                    fields[key] = value

                        events.append(fields)
                    except (ValueError, IndexError):
                        # Malformed line, skip it
                        continue
        except OSError:
            return []

        return events


def get_task_logger(task_id: str) -> TaskLogger:
    """Factory function to get a TaskLogger instance.

    Args:
        task_id: Task identifier

    Returns:
        TaskLogger instance for the task
    """
    return TaskLogger(task_id)
