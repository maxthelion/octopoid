#!/usr/bin/env python3
"""
Example: Auto-approve low-risk tasks

This script automatically approves tasks that meet certain criteria:
- Documentation tasks with commits
- Test tasks with commits

Usage:
    OCTOPOID_SERVER_URL=https://... python auto_approve.py
"""

from octopoid_sdk import OctopoidSDK


def main():
    sdk = OctopoidSDK()

    print("🔍 Checking for tasks to auto-approve...\n")

    # Get provisional tasks (awaiting review)
    tasks = sdk.tasks.list(queue="provisional")

    if not tasks:
        print("No tasks awaiting review")
        return

    print(f"Found {len(tasks)} task(s) awaiting review\n")

    approved = 0

    for task in tasks:
        # Auto-approve docs and test tasks with commits
        should_auto_approve = (
            task.get("role") in ("docs", "test")
            and task.get("commits_count", 0) > 0
        )

        if should_auto_approve:
            print(f"✅ Auto-approving {task['role']} task: {task['id']}")

            sdk.tasks.accept(task["id"], accepted_by="auto-approve-script")

            approved += 1
        else:
            role = task.get("role") or "unknown"
            print(f"⏭️  Skipping {role} task: {task['id']}")

    print(f"\n✅ Auto-approved {approved} task(s)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Error: {e}")
        exit(1)
