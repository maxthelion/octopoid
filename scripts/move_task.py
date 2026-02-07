#!/usr/bin/env python3
"""Move a task between any queues.

Usage:
    .orchestrator/venv/bin/python orchestrator/scripts/move_task.py <task-id> <target-queue>

Valid queues: incoming, claimed, breakdown, provisional, done, failed, rejected, escalated, recycled

Examples:
    move_task.py abc12345 incoming       # Retry a failed task
    move_task.py abc12345 breakdown      # Send for re-breakdown
    move_task.py abc12345 done           # Manually complete
"""

import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.config import is_db_enabled, get_orchestrator_dir
from orchestrator.db import get_connection, get_task, update_task

VALID_QUEUES = [
    "incoming", "claimed", "breakdown", "provisional",
    "done", "failed", "rejected", "escalated", "recycled",
]


def resolve_task_id(prefix: str) -> str | None:
    """Resolve a task ID prefix to a full task ID."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT id FROM tasks WHERE id LIKE ?",
            (f"{prefix}%",),
        )
        rows = cursor.fetchall()
        if len(rows) == 1:
            return rows[0]["id"]
        elif len(rows) > 1:
            print(f"Ambiguous prefix '{prefix}' matches {len(rows)} tasks:")
            for row in rows:
                print(f"  {row['id']}")
            return None
        return None


def main():
    if len(sys.argv) < 3:
        print("Usage: move_task.py <task-id> <target-queue>")
        print(f"Valid queues: {', '.join(VALID_QUEUES)}")
        sys.exit(1)

    if not is_db_enabled():
        print("Error: Database mode required")
        sys.exit(1)

    prefix = sys.argv[1]
    target_queue = sys.argv[2]

    if target_queue not in VALID_QUEUES:
        print(f"Error: Invalid queue '{target_queue}'")
        print(f"Valid queues: {', '.join(VALID_QUEUES)}")
        sys.exit(1)

    task_id = resolve_task_id(prefix)
    if not task_id:
        print(f"Task not found: {prefix}")
        sys.exit(1)

    task = get_task(task_id)
    if not task:
        print(f"Task not found: {task_id}")
        sys.exit(1)

    old_queue = task["queue"]
    if old_queue == target_queue:
        print(f"Task {task_id[:8]} is already in '{target_queue}'")
        sys.exit(0)

    # Move physical file
    queue_dir = get_orchestrator_dir() / "shared" / "queue"
    old_file = Path(task.get("file_path", ""))
    target_dir = queue_dir / target_queue
    target_dir.mkdir(parents=True, exist_ok=True)
    new_file = target_dir / f"TASK-{task_id}.md"

    if old_file.exists():
        old_file.rename(new_file)

    # Update DB
    db_updates = {"queue": target_queue, "file_path": str(new_file)}

    # Clear claim fields when moving out of claimed
    if old_queue == "claimed" and target_queue != "claimed":
        db_updates["claimed_by"] = None
        db_updates["claimed_at"] = None

    update_task(task_id, **db_updates)

    print(f"Moved task {task_id[:8]}")
    print(f"  {old_queue} -> {target_queue}")
    if "claimed_by" in db_updates:
        print("  Cleared claim info")


if __name__ == "__main__":
    main()
