#!/usr/bin/env python3
"""Cancel a task: remove from DB and archive file to cancelled/.

Usage:
    .orchestrator/venv/bin/python orchestrator/scripts/cancel_task.py <task-id>

The task file is moved to cancelled/ (not deleted) for audit trail.
"""

import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.config import is_db_enabled, get_orchestrator_dir
from orchestrator.db import get_connection, get_task, delete_task, add_history_event


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
        print("Usage: cancel_task.py <task-id>")
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

    old_queue = task["queue"]

    # Archive file to cancelled/
    queue_dir = get_orchestrator_dir() / "shared" / "queue"
    cancelled_dir = queue_dir / "cancelled"
    cancelled_dir.mkdir(parents=True, exist_ok=True)

    old_file = Path(task.get("file_path", ""))
    cancelled_file = cancelled_dir / f"TASK-{task_id}.md"

    if old_file.exists():
        old_file.rename(cancelled_file)
        print(f"  Archived: {old_file.name} -> cancelled/")

    # Record history before deleting
    add_history_event(task_id, "cancelled", details=f"from_queue={old_queue}")

    # Remove from DB
    delete_task(task_id)

    print(f"Cancelled task {task_id[:8]}")
    print(f"  Was in queue: {old_queue}")
    print(f"  File archived to: cancelled/{cancelled_file.name}")


if __name__ == "__main__":
    main()
