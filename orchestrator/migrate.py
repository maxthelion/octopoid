#!/usr/bin/env python3
"""Migration tool for transitioning from file-based to SQLite state management.

Commands:
    migrate init     - Initialize the SQLite database
    migrate import   - Import existing task files from queue directories
    migrate status   - Show migration state and database stats
    migrate rollback - Remove the database and revert to file-based system

Usage:
    python -m orchestrator.orchestrator.migrate <command>

    Note: This module must be run with -m flag due to relative imports.
"""

import argparse
import sys
from pathlib import Path

# Check for pyyaml early with clear error message
try:
    import yaml  # noqa: F401
except ImportError:
    print("Error: pyyaml is required for migration.")
    print("Install it with: pip install pyyaml")
    sys.exit(1)

from .config import get_orchestrator_dir, get_queue_dir
from .db import (
    create_task,
    get_database_path,
    get_schema_version,
    get_task_by_path,
    init_schema,
    count_tasks,
    list_tasks,
)
from .queue_utils import parse_task_file


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize the SQLite database and schema.

    Returns:
        0 on success, 1 on error
    """
    db_path = get_database_path()

    if db_path.exists() and not args.force:
        print(f"Database already exists at {db_path}")
        print("Use --force to reinitialize (WARNING: this will delete existing data)")
        return 1

    if db_path.exists() and args.force:
        print(f"Removing existing database: {db_path}")
        db_path.unlink()

    print(f"Creating database at {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    init_schema()

    version = get_schema_version()
    print(f"Database initialized successfully (schema version {version})")

    # Update .gitignore if needed
    _update_gitignore()

    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Import existing task files into the database.

    Returns:
        0 on success, 1 on error
    """
    db_path = get_database_path()
    if not db_path.exists():
        print("Database not found. Run 'migrate init' first.")
        return 1

    queue_dir = get_queue_dir()
    if not queue_dir.exists():
        print(f"Queue directory not found: {queue_dir}")
        return 1

    # Define queue directories and their database queue values
    queue_mappings = {
        "incoming": "incoming",
        "claimed": "claimed",
        "done": "done",
        "failed": "failed",
        "rejected": "rejected",
    }

    imported = 0
    skipped = 0
    errors = 0

    for subdir, db_queue in queue_mappings.items():
        subdir_path = queue_dir / subdir
        if not subdir_path.exists():
            continue

        for task_file in subdir_path.glob("*.md"):
            task_info = parse_task_file(task_file)
            if not task_info:
                print(f"  Warning: Could not parse {task_file}")
                errors += 1
                continue

            task_id = task_info["id"]

            # Check if already imported
            existing = get_task_by_path(str(task_file))
            if existing:
                if args.verbose:
                    print(f"  Skipping {task_id} (already in database)")
                skipped += 1
                continue

            try:
                create_task(
                    task_id=task_id,
                    file_path=str(task_file),
                    priority=task_info.get("priority", "P2"),
                    role=task_info.get("role"),
                    branch=task_info.get("branch", "main"),
                )

                # Update queue status if not incoming
                if db_queue != "incoming":
                    from .db import update_task
                    update_task(task_id, queue=db_queue)

                if args.verbose:
                    print(f"  Imported {task_id} ({db_queue})")
                imported += 1

            except Exception as e:
                print(f"  Error importing {task_id}: {e}")
                errors += 1

    print(f"\nImport complete:")
    print(f"  Imported: {imported}")
    print(f"  Skipped:  {skipped}")
    print(f"  Errors:   {errors}")

    return 0 if errors == 0 else 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show migration status and database statistics.

    Returns:
        0 on success
    """
    db_path = get_database_path()

    print("Migration Status")
    print("=" * 50)

    # Database status
    if db_path.exists():
        size_kb = db_path.stat().st_size / 1024
        version = get_schema_version()
        print(f"Database: {db_path}")
        print(f"  Size: {size_kb:.1f} KB")
        print(f"  Schema version: {version}")
        print()

        # Task counts by queue
        print("Tasks by queue:")
        for queue in ["incoming", "claimed", "provisional", "done", "failed", "escalated", "rejected"]:
            count = count_tasks(queue)
            if count > 0:
                print(f"  {queue}: {count}")

        total = count_tasks()
        print(f"  Total: {total}")
        print()

        # Check for blocked tasks
        blocked = [t for t in list_tasks() if t.get("blocked_by")]
        if blocked:
            print(f"Blocked tasks: {len(blocked)}")
            for t in blocked[:5]:  # Show first 5
                print(f"  {t['id']} blocked by: {t['blocked_by']}")
            if len(blocked) > 5:
                print(f"  ... and {len(blocked) - 5} more")
            print()

    else:
        print(f"Database: Not initialized")
        print()

    # File-based queue status
    queue_dir = get_queue_dir()
    if queue_dir.exists():
        print("File-based queue:")
        for subdir in ["incoming", "claimed", "done", "failed", "rejected"]:
            subdir_path = queue_dir / subdir
            if subdir_path.exists():
                count = len(list(subdir_path.glob("*.md")))
                if count > 0:
                    print(f"  {subdir}: {count}")

    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    """Remove the database and revert to file-based system.

    Returns:
        0 on success, 1 on error
    """
    db_path = get_database_path()

    if not db_path.exists():
        print("No database found to roll back.")
        return 1

    if not args.force:
        print(f"This will delete: {db_path}")
        print("Use --force to confirm rollback.")
        return 1

    # Remove database and WAL files
    db_path.unlink()
    wal_path = db_path.with_suffix(".db-wal")
    shm_path = db_path.with_suffix(".db-shm")

    if wal_path.exists():
        wal_path.unlink()
    if shm_path.exists():
        shm_path.unlink()

    print("Database removed. System reverted to file-based mode.")
    return 0


def _update_gitignore() -> None:
    """Add database files to .gitignore if not already present."""
    orchestrator_dir = get_orchestrator_dir()
    gitignore_path = orchestrator_dir.parent / ".gitignore"

    db_entries = [
        ".orchestrator/state.db",
        ".orchestrator/state.db-wal",
        ".orchestrator/state.db-shm",
    ]

    if not gitignore_path.exists():
        return

    existing = gitignore_path.read_text()
    additions = []

    for entry in db_entries:
        if entry not in existing:
            additions.append(entry)

    if additions:
        with open(gitignore_path, "a") as f:
            f.write("\n# Orchestrator database\n")
            for entry in additions:
                f.write(f"{entry}\n")
        print(f"Updated .gitignore with database entries")


def main() -> None:
    """Main entry point for the migrate CLI."""
    parser = argparse.ArgumentParser(
        description="Migrate orchestrator state between file-based and SQLite backends",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize SQLite database")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Force reinitialization (deletes existing data)",
    )

    # import command
    import_parser = subparsers.add_parser("import", help="Import existing task files")
    import_parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed import progress",
    )

    # status command
    subparsers.add_parser("status", help="Show migration status")

    # rollback command
    rollback_parser = subparsers.add_parser("rollback", help="Remove database and revert to file-based")
    rollback_parser.add_argument(
        "--force",
        action="store_true",
        help="Confirm rollback (required)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init": cmd_init,
        "import": cmd_import,
        "status": cmd_status,
        "rollback": cmd_rollback,
    }

    exit_code = commands[args.command](args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
