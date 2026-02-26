"""Agent task marker management.

This module handles task markers that link agents to specific tasks,
allowing detection of stale resume attempts.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import get_orchestrator_dir


def _get_agent_state_dir() -> Path | None:
    """Get the agent's state directory (outside worktree).

    Returns None if not running as an agent (e.g., scheduler context).
    """
    orchestrator_dir = os.environ.get("ORCHESTRATOR_DIR")
    agent_name = os.environ.get("AGENT_NAME")
    if orchestrator_dir and agent_name:
        return Path(orchestrator_dir) / "agents" / agent_name
    return None


def write_task_marker(task_id: str, task_path: Path) -> None:
    """Write a task marker file in the agent's state directory.

    This links the agent to a specific task, allowing detection
    of stale resume attempts (task completed but worktree not reset).

    The marker is stored OUTSIDE the worktree so it's not affected
    by git operations (reset, checkout, etc.).

    Args:
        task_id: Task ID being worked on
        task_path: Path to the task file
    """
    state_dir = _get_agent_state_dir()
    if not state_dir:
        return  # Not running as agent

    marker_path = state_dir / "current_task.json"
    marker_data = {
        "task_id": task_id,
        "task_path": str(task_path),
        "started_at": datetime.now().isoformat(),
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(marker_data, indent=2))


def read_task_marker_for(agent_name: str) -> dict[str, Any] | None:
    """Read the task marker file for a specific agent.

    Args:
        agent_name: Name of the agent

    Returns:
        Task marker data or None if not present
    """
    marker_path = get_orchestrator_dir() / "agents" / agent_name / "current_task.json"
    if not marker_path.exists():
        return None

    try:
        return json.loads(marker_path.read_text())
    except (IOError, json.JSONDecodeError):
        return None


def clear_task_marker_for(agent_name: str) -> None:
    """Clear the task marker file for a specific agent.

    Args:
        agent_name: Name of the agent
    """
    marker_path = get_orchestrator_dir() / "agents" / agent_name / "current_task.json"
    if marker_path.exists():
        marker_path.unlink()


def read_task_marker() -> dict[str, Any] | None:
    """Read the task marker file from agent's state directory.

    Returns:
        Task marker data or None if not present
    """
    state_dir = _get_agent_state_dir()
    if not state_dir:
        return None

    agent_name = os.environ.get("AGENT_NAME")
    if not agent_name:
        return None

    return read_task_marker_for(agent_name)


def clear_task_marker() -> None:
    """Clear the task marker file from agent's state directory."""
    state_dir = _get_agent_state_dir()
    if not state_dir:
        return

    agent_name = os.environ.get("AGENT_NAME")
    if not agent_name:
        return

    clear_task_marker_for(agent_name)
