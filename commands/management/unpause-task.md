# /unpause-task - Resume a Paused Task

Resume a paused task so it can be claimed by agents again.

## Usage

```
/unpause-task <task-id>
/unpause-task d6c94782
```

## What It Does

Clears `blocked_by` (sets to `NULL`) on the specified task using the database API.

When unpaused:
- Task becomes claimable by agents again
- Task remains in its current queue
- Will be picked up by the scheduler on next tick

## Implementation

Use Python with the orchestrator modules:

```python
#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.db import get_task, update_task, add_history_event

def main():
    task_id = sys.argv[1] if len(sys.argv) > 1 else None
    if not task_id:
        print("Usage: unpause-task <task-id>")
        sys.exit(1)

    task = get_task(task_id)
    if not task:
        print(f"Task not found: {task_id}")
        sys.exit(1)

    # Clear blocked_by
    update_task(task_id, blocked_by=None)
    add_history_event(task_id, "unpaused", details="Task unpaused by user")

    print(f"✓ Unpaused task {task_id[:8]}")
    print(f"  Title: {task['title']}")
    print(f"  Status: blocked_by = NULL (ready to claim)")

if __name__ == "__main__":
    main()
```

## Use Cases

- **Resume after manual work** - Continue automation after manual intervention
- **Dependencies resolved** - Unpause when external conditions are met
- **Re-enable processing** - Allow task to be picked up by scheduler

## Checking Status

After unpausing, the task will be available for claiming by matching agents on the next scheduler tick.

## Related Commands

- `/pause-task` - Pause a task
- `/queue-status` - Show all tasks in queues
