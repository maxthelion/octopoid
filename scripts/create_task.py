#!/usr/bin/env python3
"""Canonical task creation script.

Creates tasks by calling queue_utils.create_task() with proper validation.
All task creation should go through this script to ensure consistency.
"""

import argparse
import sys
from pathlib import Path

# Add orchestrator package to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.queue_utils import create_task


VALID_ROLES = [
    "implement",
    "test",
    "review",
    "breakdown",
    "orchestrator_impl",
    "proposer",
    "curator",
    "implementer",
    "reviewer",
    "pr_coordinator",
    "recycler",
    "queue_manager",
    "gatekeeper",
]

VALID_PRIORITIES = ["P0", "P1", "P2"]


def main():
    parser = argparse.ArgumentParser(
        description="Create a new task in the orchestrator queue"
    )
    parser.add_argument("--title", required=True, help="Task title")
    parser.add_argument(
        "--role",
        required=True,
        choices=VALID_ROLES,
        help="Target role for the task",
    )
    parser.add_argument(
        "--priority",
        default="P1",
        choices=VALID_PRIORITIES,
        help="Task priority (default: P1)",
    )
    parser.add_argument(
        "--branch", required=True, help="Base branch to work from"
    )
    parser.add_argument(
        "--context", required=True, help="Background and context"
    )
    parser.add_argument(
        "--acceptance-criteria",
        required=True,
        help="Acceptance criteria (newline-separated checklist)",
    )
    parser.add_argument(
        "--created-by", default="human", help="Who created the task (default: human)"
    )
    parser.add_argument(
        "--blocked-by",
        help="Comma-separated list of task IDs that block this task",
    )
    parser.add_argument(
        "--project-id", help="Optional parent project ID"
    )
    parser.add_argument(
        "--queue",
        default="incoming",
        help="Queue to create in (default: incoming)",
    )
    parser.add_argument(
        "--checks",
        help="Comma-separated list of check names required before human review",
    )

    args = parser.parse_args()

    # Validate required fields are not empty
    if not args.title.strip():
        print("Error: --title must not be empty", file=sys.stderr)
        sys.exit(1)

    if not args.context.strip():
        print("Error: --context must not be empty", file=sys.stderr)
        sys.exit(1)

    if not args.acceptance_criteria.strip():
        print("Error: --acceptance-criteria must not be empty", file=sys.stderr)
        sys.exit(1)

    # Parse checks if provided
    checks = None
    if args.checks:
        checks = [c.strip() for c in args.checks.split(",") if c.strip()]

    try:
        task_name = create_task(
            title=args.title,
            role=args.role,
            context=args.context,
            acceptance_criteria=args.acceptance_criteria,
            priority=args.priority,
            branch=args.branch,
            created_by=args.created_by,
            blocked_by=args.blocked_by,
            project_id=args.project_id,
            queue=args.queue,
            checks=checks,
        )

        # create_task() returns "TASK-{id}"
        print(task_name)
        sys.exit(0)

    except Exception as e:
        print(f"Error creating task: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
