#!/usr/bin/env python3
"""
Example: Create a task

Usage:
    OCTOPOID_SERVER_URL=https://... python create_task.py
"""

from octopoid_sdk import OctopoidSDK
import time

def main():
    sdk = OctopoidSDK()

    print("Creating a new task...")

    task_id = f"example-{int(time.time())}"

    task = sdk.tasks.create(
        id=task_id,
        file_path=f"tasks/incoming/TASK-{task_id}.md",
        queue="incoming",
        priority="P2",
        role="implement",
        branch="main",
    )

    print(f"✅ Created task: {task['id']}")
    print(f"   Queue: {task['queue']}")
    print(f"   Priority: {task['priority']}")
    print(f"   Role: {task['role']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Error: {e}")
        exit(1)
