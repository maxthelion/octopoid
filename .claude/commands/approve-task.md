# /approve-task - Approve a Proposed Task

Approve a task that is blocked pending human approval, allowing it to be claimed by agents.

## Usage

```
/approve-task <task-id>
```

Example:
```
/approve-task abc12345
```

## What It Does

1. Fetches the task from the server
2. Verifies that `blocked_by` is `"awaiting-approval"`
3. Clears `blocked_by` via a PATCH request so the task can be claimed
4. Confirms success

## Implementation

```python
from orchestrator.queue_utils import get_sdk

task_id = "$ARGUMENTS"  # The task ID passed to the skill

if not task_id:
    print("Error: No task ID provided. Usage: /approve-task <task-id>")
    raise SystemExit(1)

sdk = get_sdk()

# Fetch the task
task = sdk._request("GET", f"/api/v1/tasks/{task_id}")
if not task or "id" not in task:
    print(f"Error: Task '{task_id}' not found.")
    raise SystemExit(1)

# Verify it's blocked awaiting approval
blocked_by = task.get("blocked_by")
if blocked_by != "awaiting-approval":
    if blocked_by:
        print(f"Error: Task '{task_id}' is blocked by '{blocked_by}', not 'awaiting-approval'. Cannot approve.")
    else:
        print(f"Error: Task '{task_id}' is not blocked. It is already eligible to be claimed (queue: {task.get('queue', '?')}).")
    raise SystemExit(1)

title = task.get("title", task_id)
queue = task.get("queue", "?")

# Clear the blocked_by field
sdk._request("PATCH", f"/api/v1/tasks/{task_id}", json={"blocked_by": None})

print(f"Approved: [{task_id}] {title}")
print(f"Task is now unblocked in queue '{queue}' and will be claimed by the next available agent.")
```

## Notes

- This only clears `blocked_by` — it does not change the task's queue. The task must already be in `incoming` (or another claimable queue) for agents to pick it up.
- To create a task in proposed/awaiting-approval state, use `/enqueue` and indicate it should be proposed/not yet ready.

## Related Commands

- `/enqueue` — Create a new task (optionally as proposed/awaiting-approval)
- `/queue-status` — Show all tasks in the queue
