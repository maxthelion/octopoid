"""Task notes persistence across agent attempts.

This module handles saving and retrieving agent notes for tasks,
allowing context to be preserved across retry attempts.
"""

from datetime import datetime

from .config import get_notes_dir

# Max chars of stdout to save per attempt
NOTES_STDOUT_LIMIT = 3000


def get_task_notes(task_id: str) -> str | None:
    """Read accumulated agent notes for a task.

    Args:
        task_id: Task identifier (short hash)

    Returns:
        Notes content string, or None if no notes exist
    """
    notes_path = get_notes_dir() / f"TASK-{task_id}.md"
    if notes_path.exists():
        try:
            return notes_path.read_text()
        except IOError:
            return None
    return None


def save_task_notes(
    task_id: str,
    agent_name: str,
    stdout: str,
    commits: int = 0,
    turns: int = 0,
) -> None:
    """Append a run summary to the notes file for a task.

    Each call adds a new attempt section with metadata and a tail
    of stdout (last NOTES_STDOUT_LIMIT chars).

    Args:
        task_id: Task identifier
        agent_name: Name of the agent that ran
        stdout: Full stdout from Claude invocation
        commits: Commits made this attempt
        turns: Turns used this attempt
    """
    notes_dir = get_notes_dir()
    notes_dir.mkdir(parents=True, exist_ok=True)
    notes_path = notes_dir / f"TASK-{task_id}.md"

    # Count existing attempts
    attempt = 1
    if notes_path.exists():
        try:
            existing = notes_path.read_text()
            attempt = existing.count("## Attempt ") + 1
        except IOError:
            pass

    # Truncate stdout to tail
    stdout_tail = stdout[-NOTES_STDOUT_LIMIT:] if len(stdout) > NOTES_STDOUT_LIMIT else stdout
    if len(stdout) > NOTES_STDOUT_LIMIT:
        stdout_tail = f"[...truncated {len(stdout) - NOTES_STDOUT_LIMIT} chars...]\n" + stdout_tail

    timestamp = datetime.now().isoformat()
    section = f"""
## Attempt {attempt} — {agent_name} ({timestamp})
Turns: {turns} | Commits: {commits}

{stdout_tail.strip()}

"""

    with open(notes_path, "a") as f:
        # Write header on first attempt
        if attempt == 1:
            f.write(f"# Agent Notes: TASK-{task_id}\n")
        f.write(section)


def cleanup_task_notes(task_id: str) -> bool:
    """Delete notes file for a completed task.

    Args:
        task_id: Task identifier

    Returns:
        True if notes file existed and was deleted
    """
    notes_path = get_notes_dir() / f"TASK-{task_id}.md"
    if notes_path.exists():
        try:
            notes_path.unlink()
            return True
        except IOError:
            return False
    return False
