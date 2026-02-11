#!/usr/bin/env python3
"""
Clean up test data from the Octopoid database.

Removes tasks that are clearly test data:
- IDs starting with 'test-'
- IDs starting with 'task-' (generic task IDs from testing)
- Tasks with titles containing 'test' (case-insensitive)

Usage:
    python scripts/cleanup-test-data.py --server http://localhost:8787
    python scripts/cleanup-test-data.py --server http://localhost:8787 --dry-run
"""

import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from octopoid_sdk import OctopoidSDK


def is_test_task(task: dict) -> bool:
    """Determine if a task is test data."""
    task_id = task['id']
    title = (task.get('title') or '').lower()

    # Test ID patterns
    if task_id.startswith('test-'):
        return True
    if task_id.startswith('task-') and len(task_id.split('-')) == 3:
        # Generic task-{random}-{random} format
        return True

    # Test titles
    if 'test' in title and not task_id.startswith('gh-'):
        # Contains 'test' but not a GitHub issue
        return True

    return False


def main():
    parser = argparse.ArgumentParser(description='Clean up test data from Octopoid')
    parser.add_argument('--server', required=True, help='Octopoid server URL')
    parser.add_argument('--api-key', help='API key for authentication')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be deleted without deleting')
    args = parser.parse_args()

    # Connect to server
    sdk = OctopoidSDK(server_url=args.server, api_key=args.api_key)

    # Get all tasks
    print("Fetching all tasks...")
    all_tasks = sdk.tasks.list()

    # Find test tasks
    test_tasks = [t for t in all_tasks if is_test_task(t)]

    if not test_tasks:
        print("✓ No test data found!")
        return 0

    print(f"\nFound {len(test_tasks)} test tasks:\n")
    for task in test_tasks:
        title = task.get('title', 'No title')
        queue = task.get('queue', 'unknown')
        print(f"  - {task['id']}: {title} (queue: {queue})")

    if args.dry_run:
        print("\n[DRY RUN] No tasks were deleted.")
        return 0

    # Confirm deletion
    print(f"\nAbout to delete {len(test_tasks)} tasks. Continue? [y/N] ", end='')
    response = input().strip().lower()

    if response != 'y':
        print("Cancelled.")
        return 0

    # Delete tasks
    print("\nDeleting tasks...")
    deleted = 0
    failed = 0

    for task in test_tasks:
        try:
            sdk.tasks.delete(task['id'])
            print(f"  ✓ Deleted {task['id']}")
            deleted += 1
        except Exception as e:
            print(f"  ✗ Failed to delete {task['id']}: {e}")
            failed += 1

    print(f"\nComplete: {deleted} deleted, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
