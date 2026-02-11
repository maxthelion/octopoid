#!/usr/bin/env python3
"""Helper script to extract information from task logs.

Usage:
    task-log-info.py <task-id> claims      # Count claims
    task-log-info.py <task-id> first-claim # First claim timestamp
    task-log-info.py <task-id> last-claim  # Last claim timestamp
"""

import sys
from pathlib import Path
from datetime import datetime


def get_orchestrator_dir() -> Path:
    """Get .orchestrator directory relative to this script."""
    script_dir = Path(__file__).parent
    return script_dir.parent / ".orchestrator"


def get_task_log_path(task_id: str) -> Path:
    """Get path to task log file."""
    logs_dir = get_orchestrator_dir() / "logs" / "tasks"
    return logs_dir / f"TASK-{task_id}.log"


def count_claims(task_id: str) -> int:
    """Count how many times a task has been claimed."""
    log_path = get_task_log_path(task_id)
    if not log_path.exists():
        return 0

    count = 0
    with open(log_path, 'r') as f:
        for line in f:
            if ' CLAIMED ' in line:
                count += 1
    return count


def get_claim_times(task_id: str) -> tuple[str | None, str | None]:
    """Get first and last claim timestamps as ISO strings."""
    log_path = get_task_log_path(task_id)
    if not log_path.exists():
        return (None, None)

    first_claim = None
    last_claim = None

    with open(log_path, 'r') as f:
        for line in f:
            if ' CLAIMED ' in line and line.startswith('['):
                # Extract timestamp from [2026-02-11T10:16:17] format
                end_idx = line.index(']')
                timestamp_str = line[1:end_idx]
                if first_claim is None:
                    first_claim = timestamp_str
                last_claim = timestamp_str

    return (first_claim, last_claim)


def format_time_ago(timestamp_str: str | None) -> str:
    """Format timestamp as 'X min/hours/days ago'."""
    if not timestamp_str:
        return "never"

    try:
        timestamp = datetime.fromisoformat(timestamp_str)
        now = datetime.now()
        delta = now - timestamp

        if delta.days > 0:
            return f"{delta.days}d ago"
        elif delta.seconds >= 3600:
            hours = delta.seconds // 3600
            return f"{hours}h ago"
        else:
            minutes = delta.seconds // 60
            return f"{minutes}m ago"
    except ValueError:
        return timestamp_str


def main():
    if len(sys.argv) < 3:
        print("Usage: task-log-info.py <task-id> {claims|first-claim|last-claim|first-claim-ago|last-claim-ago}", file=sys.stderr)
        sys.exit(1)

    task_id = sys.argv[1]
    # Remove TASK- prefix if present
    if task_id.startswith("TASK-"):
        task_id = task_id[5:]
    # Remove .md suffix if present
    if task_id.endswith(".md"):
        task_id = task_id[:-3]

    command = sys.argv[2]

    if command == "claims":
        print(count_claims(task_id))
    elif command == "first-claim":
        first, _ = get_claim_times(task_id)
        print(first or "")
    elif command == "last-claim":
        _, last = get_claim_times(task_id)
        print(last or "")
    elif command == "first-claim-ago":
        first, _ = get_claim_times(task_id)
        print(format_time_ago(first))
    elif command == "last-claim-ago":
        _, last = get_claim_times(task_id)
        print(format_time_ago(last))
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
