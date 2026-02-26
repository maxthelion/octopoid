"""Task message thread persistence across agent attempts.

Stores rejection messages and feedback as a thread of messages for a task,
allowing the full history to be included when spawning the next agent.

Messages are stored as JSONL (one JSON object per line) in the shared dir.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import get_shared_dir


def get_threads_dir() -> Path:
    """Get the directory for task thread files."""
    threads_dir = get_shared_dir() / "threads"
    threads_dir.mkdir(parents=True, exist_ok=True)
    return threads_dir


def post_message(
    task_id: str,
    role: str,
    content: str,
    author: str | None = None,
) -> None:
    """Append a message to the task's thread.

    Args:
        task_id: Task identifier (short hash)
        role: Message role, e.g. 'rejection', 'info'
        content: Message body (markdown)
        author: Who posted the message (e.g. 'gatekeeper', 'scheduler')
    """
    threads_dir = get_threads_dir()
    thread_path = threads_dir / f"TASK-{task_id}.jsonl"

    message: dict[str, Any] = {
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat(),
    }
    if author:
        message["author"] = author

    with open(thread_path, "a") as f:
        f.write(json.dumps(message) + "\n")


def get_thread(task_id: str) -> list[dict[str, Any]]:
    """Read all messages from a task's thread.

    Args:
        task_id: Task identifier

    Returns:
        List of message dicts, in chronological order
    """
    threads_dir = get_threads_dir()
    thread_path = threads_dir / f"TASK-{task_id}.jsonl"

    if not thread_path.exists():
        return []

    messages = []
    try:
        for line in thread_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # Skip malformed lines
    except IOError:
        pass

    return messages


def format_thread_for_prompt(messages: list[dict[str, Any]]) -> str:
    """Format a message thread as a markdown section for the agent prompt.

    Args:
        messages: List of message dicts from get_thread()

    Returns:
        Formatted markdown string, or empty string if no messages
    """
    if not messages:
        return ""

    rejections = [m for m in messages if m.get("role") == "rejection"]
    if not rejections:
        return ""

    lines = ["## Previous Rejection Feedback"]
    lines.append("")
    lines.append(
        "**This task was previously attempted and rejected.**"
        " Read the feedback below carefully before starting."
    )
    lines.append("")

    for i, msg in enumerate(rejections, 1):
        timestamp = msg.get("timestamp", "")
        author = msg.get("author", "gatekeeper")
        date_str = ""
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp)
                date_str = f" ({dt.strftime('%Y-%m-%d %H:%M')})"
            except ValueError:
                pass

        lines.append(f"### Rejection #{i}{date_str} — by {author}")
        lines.append("")
        lines.append(msg.get("content", ""))
        lines.append("")

    return "\n".join(lines)


def cleanup_thread(task_id: str) -> bool:
    """Delete the thread file for a completed task.

    Args:
        task_id: Task identifier

    Returns:
        True if thread file existed and was deleted
    """
    threads_dir = get_threads_dir()
    thread_path = threads_dir / f"TASK-{task_id}.jsonl"

    if thread_path.exists():
        try:
            thread_path.unlink()
            return True
        except IOError:
            return False
    return False
