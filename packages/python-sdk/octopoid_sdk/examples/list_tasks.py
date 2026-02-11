#!/usr/bin/env python3
"""
Example: List tasks by queue

Usage:
    OCTOPOID_SERVER_URL=https://... python list_tasks.py
"""

from octopoid_sdk import OctopoidSDK


def main():
    sdk = OctopoidSDK()

    print("📊 Task Summary\n")

    # Get tasks by queue
    queues = ["incoming", "claimed", "provisional", "done"]

    for queue in queues:
        tasks = sdk.tasks.list(queue=queue, limit=100)

        print(f"{queue.upper()}: {len(tasks)}")

        if tasks:
            # Show first 3 tasks
            for task in tasks[:3]:
                role = task.get("role") or "no role"
                print(f"  • {task['id']} ({task['priority']}) - {role}")

            if len(tasks) > 3:
                print(f"  ... and {len(tasks) - 3} more")

        print()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Error: {e}")
        exit(1)
