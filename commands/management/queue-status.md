# /queue-status - Show Queue State

Display the current state of the task queue.

## Usage

```
/queue-status
```

## What It Shows

### Queue Counts
```
Queue Status
============
Incoming:  5 tasks
Claimed:   2 tasks
Done:      12 tasks
Failed:    1 task
Open PRs:  3
```

### Queue Limits
```
Limits
------
Max Incoming: 20
Max Claimed:  5
Max Open PRs: 10
```

### Task Details

For each queue, shows tasks sorted by priority:

```
Incoming Tasks
--------------
P0 | TASK-abc123 | Fix security vulnerability | 2h ago
P1 | TASK-def456 | Add user dashboard        | 5h ago
P2 | TASK-ghi789 | Update dependencies       | 1d ago

Claimed Tasks
-------------
TASK-jkl012 | Implement auth | impl-agent-1 | claimed 10m ago

Failed Tasks
------------
TASK-mno345 | Add logging | Error: Test failures | failed 3h ago
```

## Implementation

To get queue status programmatically:

```python
from orchestrator.orchestrator.queue_utils import get_queue_status

status = get_queue_status()
print(f"Incoming: {status['incoming']['count']}")
print(f"Claimed: {status['claimed']['count']}")
print(f"Open PRs: {status['open_prs']}")
```

## Related Commands

- `/enqueue` - Add new task
- `/agent-status` - Show agent states
- `/retry-failed` - Retry failed tasks
