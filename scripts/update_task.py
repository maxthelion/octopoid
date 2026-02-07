#!/usr/bin/env python3
"""Update task metadata (role, priority) in both DB and task file.

Usage:
    .orchestrator/venv/bin/python orchestrator/scripts/update_task.py <task-id> [--role ROLE] [--priority PRIORITY]

Examples:
    update_task.py abc12345 --role implement
    update_task.py abc12345 --priority P0
    update_task.py abc12345 --role breakdown --priority P1
"""

import argparse
import re
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.config import is_db_enabled
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


def update_task_file(file_path: str, role: str | None, priority: str | None) -> bool:
    """Update metadata fields in the task markdown file."""
    path = Path(file_path)
    if not path.exists():
        return False

    content = path.read_text()
    changed = False

    if role:
        new_content = re.sub(r"^ROLE:\s*.+$", f"ROLE: {role}", content, count=1, flags=re.MULTILINE)
        if new_content != content:
            content = new_content
            changed = True

    if priority:
        new_content = re.sub(r"^PRIORITY:\s*.+$", f"PRIORITY: {priority}", content, count=1, flags=re.MULTILINE)
        if new_content != content:
            content = new_content
            changed = True

    if changed:
        path.write_text(content)

    return changed


def main():
    parser = argparse.ArgumentParser(description="Update task metadata")
    parser.add_argument("task_id", help="Task ID (short or full)")
    parser.add_argument("--role", "-r", help="New role (implement, breakdown, test, review, orchestrator_impl)")
    parser.add_argument("--priority", "-p", choices=["P0", "P1", "P2"], help="New priority")
    args = parser.parse_args()

    if not args.role and not args.priority:
        print("Error: Specify at least one of --role or --priority")
        sys.exit(1)

    if not is_db_enabled():
        print("Error: Database mode required")
        sys.exit(1)

    task_id = resolve_task_id(args.task_id)
    if not task_id:
        print(f"Task not found: {args.task_id}")
        sys.exit(1)

    task = get_task(task_id)
    if not task:
        print(f"Task not found: {task_id}")
        sys.exit(1)

    old_role = task.get("role", "N/A")
    old_priority = task.get("priority", "N/A")

    # Update DB
    db_updates = {}
    if args.role:
        db_updates["role"] = args.role
    if args.priority:
        db_updates["priority"] = args.priority

    update_task(task_id, **db_updates)

    # Update task file
    file_path = task.get("file_path", "")
    if file_path:
        update_task_file(file_path, args.role, args.priority)

    print(f"Updated task {task_id[:8]}")
    if args.role:
        print(f"  Role: {old_role} -> {args.role}")
    if args.priority:
        print(f"  Priority: {old_priority} -> {args.priority}")


if __name__ == "__main__":
    main()
