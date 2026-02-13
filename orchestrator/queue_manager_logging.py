"""Logging for queue manager auto-fixes.

Provides centralized logging for queue health auto-fixes so all actions
are recorded and can be reviewed by humans.
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

# Add orchestrator to path if running as script
if __name__ == "__main__":
    SCRIPT_DIR = Path(__file__).parent
    sys.path.insert(0, str(SCRIPT_DIR.parent))

from orchestrator.config import get_orchestrator_dir  # noqa: E402


FixType = Literal[
    "file-db-sync",
    "orphan-fix",
    "stale-error",
    "escalate",
]


class QueueManagerLogger:
    """Logger for queue manager auto-fix actions."""

    def __init__(self, log_dir: Path | None = None):
        """Initialize logger.

        Args:
            log_dir: Directory for log files (defaults to .octopoid/logs/)
        """
        if log_dir is None:
            log_dir = get_orchestrator_dir() / "logs"

        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Get today's log file
        today = datetime.now().strftime("%Y-%m-%d")
        self.log_file = self.log_dir / f"queue-manager-{today}.log"

        # Track actions in memory for summary
        self.actions: list[dict] = []

    def log(self, fix_type: FixType, message: str) -> None:
        """Log an auto-fix action.

        Args:
            fix_type: Type of fix applied
            message: Human-readable message describing the action
        """
        timestamp = datetime.now().isoformat()
        log_entry = f"[{timestamp}] [{fix_type}] {message}\n"

        # Append to log file
        with open(self.log_file, "a") as f:
            f.write(log_entry)

        # Track in memory
        self.actions.append({
            "timestamp": timestamp,
            "fix_type": fix_type,
            "message": message,
        })

    def get_summary(self) -> dict[str, int]:
        """Get summary of actions by type.

        Returns:
            Dict mapping fix_type -> count
        """
        summary = {
            "file-db-sync": 0,
            "orphan-fix": 0,
            "stale-error": 0,
            "escalate": 0,
        }

        for action in self.actions:
            fix_type = action["fix_type"]
            if fix_type in summary:
                summary[fix_type] += 1

        return summary

    def write_notes_summary(self, notes_dir: Path | None = None) -> Path:
        """Write a summary of actions to the notes directory.

        Args:
            notes_dir: Directory for notes (defaults to .octopoid/shared/notes/)

        Returns:
            Path to the written notes file
        """
        if notes_dir is None:
            notes_dir = get_orchestrator_dir() / "shared" / "notes"

        notes_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        notes_file = notes_dir / f"queue-manager-{timestamp}.md"

        summary = self.get_summary()

        content = f"""# Queue Manager Auto-Fix Summary

**Generated:** {datetime.now().isoformat()}
**Log file:** {self.log_file}

## Summary

- File-DB syncs: {summary['file-db-sync']}
- Orphan files registered: {summary['orphan-fix']}
- Stale errors cleaned: {summary['stale-error']}
- Issues escalated: {summary['escalate']}

## Actions Taken

"""

        if not self.actions:
            content += "No actions taken.\n"
        else:
            for action in self.actions:
                content += f"### [{action['fix_type']}] {action['timestamp']}\n"
                content += f"{action['message']}\n\n"

        with open(notes_file, "w") as f:
            f.write(content)

        return notes_file


def get_recent_fixes(hours: int = 24, log_dir: Path | None = None) -> list[dict]:
    """Get recent auto-fix actions from log files.

    Args:
        hours: How many hours to look back
        log_dir: Directory containing log files (defaults to .octopoid/logs/)

    Returns:
        List of actions with timestamp, fix_type, and message
    """
    if log_dir is None:
        log_dir = get_orchestrator_dir() / "logs"

    if not log_dir.exists():
        return []

    # Calculate cutoff time
    cutoff = datetime.now().timestamp() - (hours * 3600)

    actions = []

    # Check all queue-manager log files (sorted newest first)
    log_files = sorted(
        log_dir.glob("queue-manager-*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    for log_file in log_files:
        # Skip if file is too old
        if log_file.stat().st_mtime < cutoff:
            break

        with open(log_file) as f:
            for line in f:
                # Parse log line: [timestamp] [fix_type] message
                if not line.startswith("["):
                    continue

                try:
                    # Extract timestamp
                    ts_end = line.index("]", 1)
                    timestamp_str = line[1:ts_end]
                    timestamp = datetime.fromisoformat(timestamp_str)

                    # Check if within time window
                    if timestamp.timestamp() < cutoff:
                        continue

                    # Extract fix_type
                    type_start = line.index("[", ts_end) + 1
                    type_end = line.index("]", type_start)
                    fix_type = line[type_start:type_end]

                    # Extract message
                    message = line[type_end + 2:].strip()

                    actions.append({
                        "timestamp": timestamp_str,
                        "fix_type": fix_type,
                        "message": message,
                    })
                except (ValueError, IndexError):
                    # Skip malformed lines
                    continue

    # Sort by timestamp (newest first)
    actions.sort(key=lambda a: a["timestamp"], reverse=True)

    return actions
