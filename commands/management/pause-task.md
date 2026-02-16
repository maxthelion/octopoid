# /pause-task - Pause a Task

Temporarily pause a task to prevent it from being claimed by agents.

## Usage

```
/pause-task <task-id>
/pause-task d6c94782
```

## What It Does

Sets `blocked_by` to `"paused"` on the specified task using the database API.

When paused:
- Task won't be claimed by any agent
- Task remains in its current queue
- Can be resumed later with `/unpause-task`
- State is preserved

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
        print("Usage: pause-task <task-id>")
        sys.exit(1)

    task = get_task(task_id)
    if not task:
        print(f"Task not found: {task_id}")
        sys.exit(1)

    # Update blocked_by to "paused"
    update_task(task_id, blocked_by="paused")
    add_history_event(task_id, "paused", details="Task paused by user")

    print(f"✓ Paused task {task_id[:8]}")
    print(f"  Title: {task['title']}")
    print(f"  Status: blocked_by = paused")

if __name__ == "__main__":
    main()
```

## Use Cases

- **Manual intervention** - Pause a task while you work on it manually
- **Waiting for dependencies** - Pause until external conditions are met
- **Debugging** - Prevent a task from being picked up while investigating
- **Resource management** - Temporarily pause low-priority tasks

## Resuming a Task

```
/unpause-task <task-id>
```

## Checking Status

Tasks with `blocked_by = "paused"` will appear in task status views but won't be claimed by agents.

## Related Commands

- `/unpause-task` - Resume a paused task
- `/queue-status` - Show all tasks in queues
