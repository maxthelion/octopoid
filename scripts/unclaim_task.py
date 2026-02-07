#!/usr/bin/env python3
"""Move a claimed task back to incoming, clearing claim metadata.

Usage:
    .orchestrator/venv/bin/python orchestrator/scripts/unclaim_task.py <task-id>

Supports task ID prefix matching (first 8+ chars).
"""

import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.config import is_db_enabled, get_orchestrator_dir
from orchestrator.db import get_connection, get_task, update_task


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
    if len(sys.argv) < 2:
        print("Usage: unclaim_task.py <task-id>")
        sys.exit(1)

    if not is_db_enabled():
        print("Error: Database mode required")
        sys.exit(1)

    prefix = sys.argv[1]
    task_id = resolve_task_id(prefix)

    if not task_id:
        print(f"Task not found: {prefix}")
        sys.exit(1)

    task = get_task(task_id)
    if not task:
        print(f"Task not found: {task_id}")
        sys.exit(1)

    if task["queue"] != "claimed":
        print(f"Error: Task {task_id[:8]} is in '{task['queue']}' queue, not 'claimed'")
        sys.exit(1)

    claimed_by = task.get("claimed_by", "unknown")

    # Update DB: move to incoming, clear claim fields
    update_task(
        task_id,
        queue="incoming",
        claimed_by=None,
        claimed_at=None,
    )

    # Move file from claimed/ to incoming/
    queue_dir = get_orchestrator_dir() / "shared" / "queue"
    claimed_file = queue_dir / "claimed" / f"TASK-{task_id}.md"
    incoming_dir = queue_dir / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    incoming_file = incoming_dir / f"TASK-{task_id}.md"

    if claimed_file.exists():
        claimed_file.rename(incoming_file)
        # Update file_path in DB
        update_task(task_id, file_path=str(incoming_file))

    print(f"Unclaimed task {task_id[:8]}")
    print(f"  Previously claimed by: {claimed_by}")
    print(f"  Moved: claimed -> incoming")


if __name__ == "__main__":
    main()
