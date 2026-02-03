"""Message utilities for agent-to-human communication."""

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from .config import get_orchestrator_dir

MessageType = Literal["info", "warning", "error", "question"]


def get_messages_dir() -> Path:
    """Get the messages directory."""
    messages_dir = get_orchestrator_dir() / "messages"
    messages_dir.mkdir(parents=True, exist_ok=True)
    return messages_dir


def create_message(
    message_type: MessageType,
    title: str,
    body: str,
    agent_name: str | None = None,
    task_id: str | None = None,
) -> Path:
    """Create a message file for the user to see.

    Args:
        message_type: One of 'info', 'warning', 'error', 'question'
        title: Short title for the message
        body: Full message content (markdown)
        agent_name: Name of the agent creating the message
        task_id: Related task ID if applicable

    Returns:
        Path to the created message file
    """
    messages_dir = get_messages_dir()
    timestamp = datetime.now()

    # Create filename: TYPE-TIMESTAMP-TITLE.md
    safe_title = "".join(c if c.isalnum() or c in "-_" else "-" for c in title[:30])
    filename = f"{message_type}-{timestamp.strftime('%Y%m%d-%H%M%S')}-{safe_title}.md"

    # Build message content
    type_emoji = {
        "info": "ℹ️",
        "warning": "⚠️",
        "error": "❌",
        "question": "❓",
    }

    lines = [
        f"# {type_emoji.get(message_type, '')} {title}",
        "",
        f"**Type:** {message_type}",
        f"**Time:** {timestamp.isoformat()}",
    ]

    if agent_name:
        lines.append(f"**From:** {agent_name}")
    if task_id:
        lines.append(f"**Task:** {task_id}")

    lines.extend(["", "---", "", body])

    content = "\n".join(lines)

    # Write atomically
    fd, temp_path = tempfile.mkstemp(dir=messages_dir, suffix=".md")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)

        dest = messages_dir / filename
        os.rename(temp_path, dest)
        return dest
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def info(title: str, body: str, agent_name: str | None = None, task_id: str | None = None) -> Path:
    """Create an info message."""
    return create_message("info", title, body, agent_name, task_id)


def warning(title: str, body: str, agent_name: str | None = None, task_id: str | None = None) -> Path:
    """Create a warning message."""
    return create_message("warning", title, body, agent_name, task_id)


def error(title: str, body: str, agent_name: str | None = None, task_id: str | None = None) -> Path:
    """Create an error message."""
    return create_message("error", title, body, agent_name, task_id)


def question(title: str, body: str, agent_name: str | None = None, task_id: str | None = None) -> Path:
    """Create a question message (agent needs human input)."""
    return create_message("question", title, body, agent_name, task_id)


def list_messages(message_type: MessageType | None = None) -> list[dict]:
    """List all messages, optionally filtered by type.

    Args:
        message_type: Filter by type, or None for all

    Returns:
        List of message dictionaries with path, type, title, time
    """
    messages_dir = get_messages_dir()
    messages = []

    for msg_file in messages_dir.glob("*.md"):
        # Parse filename: TYPE-TIMESTAMP-TITLE.md
        parts = msg_file.stem.split("-", 3)
        if len(parts) >= 3:
            msg_type = parts[0]

            if message_type and msg_type != message_type:
                continue

            messages.append({
                "path": msg_file,
                "type": msg_type,
                "filename": msg_file.name,
                "created": msg_file.stat().st_mtime,
            })

    # Sort by creation time, newest first
    messages.sort(key=lambda m: m["created"], reverse=True)
    return messages


def clear_messages(message_type: MessageType | None = None, older_than_hours: int | None = None) -> int:
    """Clear messages from the messages directory.

    Args:
        message_type: Only clear this type, or None for all
        older_than_hours: Only clear messages older than this

    Returns:
        Number of messages cleared
    """
    messages_dir = get_messages_dir()
    cleared = 0
    now = datetime.now().timestamp()

    for msg_file in messages_dir.glob("*.md"):
        # Check type filter
        if message_type:
            parts = msg_file.stem.split("-", 1)
            if parts[0] != message_type:
                continue

        # Check age filter
        if older_than_hours:
            age_hours = (now - msg_file.stat().st_mtime) / 3600
            if age_hours < older_than_hours:
                continue

        msg_file.unlink()
        cleared += 1

    return cleared
