#!/usr/bin/env python3
"""Approve a task: accept completion, merge PR, delete remote branch, unblock dependents.

Usage:
    .orchestrator/venv/bin/python orchestrator/scripts/approve_task.py <task-id-or-pr-number>

Accepts either a task ID prefix or a PR number (numeric).

Examples:
    approve_task.py abc12345     # By task ID prefix
    approve_task.py 42           # By PR number
"""

import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.config import is_db_enabled
from orchestrator.db import get_connection, get_task
from orchestrator.queue_utils import approve_and_merge


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


def resolve_by_pr_number(pr_number: int) -> str | None:
    """Find a task by its PR number."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT id FROM tasks WHERE pr_number = ?",
            (pr_number,),
        )
        row = cursor.fetchone()
        return row["id"] if row else None


def main():
    if len(sys.argv) < 2:
        print("Usage: approve_task.py <task-id-or-pr-number>")
        sys.exit(1)

    if not is_db_enabled():
        print("Error: Database mode required")
        sys.exit(1)

    identifier = sys.argv[1]

    # Determine if identifier is a PR number or task ID
    task_id = None
    if identifier.isdigit():
        pr_number = int(identifier)
        task_id = resolve_by_pr_number(pr_number)
        if not task_id:
            # Could also be a task ID that happens to be all digits
            task_id = resolve_task_id(identifier)
        if not task_id:
            print(f"No task found for PR #{pr_number} or task ID prefix '{identifier}'")
            sys.exit(1)
    else:
        task_id = resolve_task_id(identifier)
        if not task_id:
            print(f"Task not found: {identifier}")
            sys.exit(1)

    task = get_task(task_id)
    if not task:
        print(f"Task not found: {task_id}")
        sys.exit(1)

    pr_number = task.get("pr_number")
    pr_url = task.get("pr_url")

    print(f"Approving task {task_id[:8]}")
    print(f"  Queue: {task['queue']}")
    if pr_url:
        print(f"  PR: {pr_url}")
    elif pr_number:
        print(f"  PR: #{pr_number}")

    result = approve_and_merge(task_id, merge_method="merge")

    if result.get("error"):
        print(f"  Error: {result['error']}")
        sys.exit(1)

    if result.get("merged"):
        print(f"  PR merged successfully")
    elif result.get("merge_error"):
        print(f"  PR merge failed: {result['merge_error']}")
        print(f"  Task still moved to done")
    elif not pr_number:
        print(f"  No PR associated (task moved to done)")

    print(f"  Task -> done")


if __name__ == "__main__":
    main()
