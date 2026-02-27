# /cancel-task - Cancel a Task

Kill an agent process, remove its worktree and runtime files, and delete the task from the server.

## Usage

```
/cancel-task <task-id>
```

**task-id** is the short 8-character hex ID (e.g. `a7517c0d`). You can find it with `/queue-status`.

## Implementation

### Step 1: Resolve task ID

Extract the task ID from the user's message. Strip any `TASK-` prefix if present.

### Step 2: Run cancel_task

```python
import sys
from octopoid.tasks import cancel_task

task_id = "<task-id>"  # replace with actual task ID

result = cancel_task(task_id)

print(f"Cancel result for task {task_id}:")
print(f"  Killed PID:       {result['killed_pid'] or 'none (no running agent found)'}")
print(f"  Worktree removed: {result['worktree_removed']}")
print(f"  Runtime removed:  {result['runtime_removed']}")
print(f"  Server deleted:   {result['server_deleted']}")
if result['errors']:
    print(f"  Errors:")
    for err in result['errors']:
        print(f"    - {err}")
else:
    print(f"  No errors.")
```

### Step 3: Report results

Display the output to the user. If there were errors, explain what they mean and whether any manual cleanup is needed.

Common outcomes:
- **killed_pid: none** — No running agent was found for this task. Either the agent already exited, or the task was never claimed.
- **worktree_removed: False** — The git worktree could not be removed. This might mean the worktree directory doesn't exist (harmless) or git failed (check errors).
- **server_deleted: False** — The server record could not be deleted. Check errors for the reason.

If all four steps succeeded, the task is fully cleaned up.

## Related Commands

- `/queue-status` — Find task IDs and diagnose queue state
- `/enqueue` — Create a new task
