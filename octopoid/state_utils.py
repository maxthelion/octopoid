"""Agent state management with atomic file operations."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class AgentState:
    """State of an agent tracked in state.json."""

    running: bool = False
    pid: int | None = None
    last_started: str | None = None  # ISO8601 timestamp
    last_finished: str | None = None  # ISO8601 timestamp
    last_exit_code: int | None = None
    consecutive_failures: int = 0
    total_runs: int = 0
    total_successes: int = 0
    total_failures: int = 0
    current_task: str | None = None  # Task ID if working on one
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentState":
        """Create AgentState from dictionary."""
        known_fields = {
            "running",
            "pid",
            "last_started",
            "last_finished",
            "last_exit_code",
            "consecutive_failures",
            "total_runs",
            "total_successes",
            "total_failures",
            "current_task",
            "extra",
        }

        kwargs = {k: v for k, v in data.items() if k in known_fields}

        # Store unknown fields in extra
        extra = kwargs.get("extra", {})
        for k, v in data.items():
            if k not in known_fields:
                extra[k] = v
        kwargs["extra"] = extra

        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


def load_state(state_path: Path | str) -> AgentState:
    """Load agent state from file.

    Args:
        state_path: Path to state.json

    Returns:
        AgentState instance (default values if file doesn't exist)
    """
    state_path = Path(state_path)

    if not state_path.exists():
        return AgentState()

    try:
        with open(state_path) as f:
            data = json.load(f)
        return AgentState.from_dict(data)
    except (json.JSONDecodeError, IOError):
        return AgentState()


def save_state(state: AgentState, state_path: Path | str) -> None:
    """Save agent state atomically using temp file + rename.

    Args:
        state: AgentState to save
        state_path: Path to state.json
    """
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in same directory (ensures same filesystem for atomic rename)
    fd, temp_path = tempfile.mkstemp(
        dir=state_path.parent, prefix=".state_", suffix=".json"
    )

    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state.to_dict(), f, indent=2)

        # Atomic rename
        os.rename(temp_path, state_path)
    except Exception:
        # Clean up temp file on error
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def is_overdue(state: AgentState, interval_seconds: int) -> bool:
    """Check if an agent is due to run based on its interval.

    Args:
        state: Current agent state
        interval_seconds: How often the agent should run

    Returns:
        True if agent should run (never run or last run was > interval_seconds ago)
    """
    if state.last_started is None:
        return True

    try:
        last_started = datetime.fromisoformat(state.last_started)
        now = datetime.now()
        elapsed = (now - last_started).total_seconds()
        return elapsed >= interval_seconds
    except (ValueError, TypeError):
        # Invalid timestamp, consider overdue
        return True


def mark_started(state: AgentState, pid: int, task_id: str | None = None) -> AgentState:
    """Update state to indicate agent has started.

    Args:
        state: Current state
        pid: Process ID of the agent
        task_id: Optional task being worked on

    Returns:
        Updated state (new instance)
    """
    return AgentState(
        running=True,
        pid=pid,
        last_started=datetime.now().isoformat(),
        last_finished=state.last_finished,
        last_exit_code=state.last_exit_code,
        consecutive_failures=state.consecutive_failures,
        total_runs=state.total_runs + 1,
        total_successes=state.total_successes,
        total_failures=state.total_failures,
        current_task=task_id,
        extra=state.extra,
    )


def mark_finished(state: AgentState, exit_code: int) -> AgentState:
    """Update state to indicate agent has finished.

    Args:
        state: Current state
        exit_code: Exit code of the agent process

    Returns:
        Updated state (new instance)
    """
    success = exit_code == 0

    return AgentState(
        running=False,
        pid=None,
        last_started=state.last_started,
        last_finished=datetime.now().isoformat(),
        last_exit_code=exit_code,
        consecutive_failures=0 if success else state.consecutive_failures + 1,
        total_runs=state.total_runs,
        total_successes=state.total_successes + (1 if success else 0),
        total_failures=state.total_failures + (0 if success else 1),
        current_task=None,
        extra=state.extra,
    )


def is_process_running(pid: int | None) -> bool:
    """Check if a process is still running.

    Args:
        pid: Process ID to check

    Returns:
        True if process exists and is running
    """
    if pid is None:
        return False

    try:
        # Sending signal 0 checks if process exists without affecting it
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
