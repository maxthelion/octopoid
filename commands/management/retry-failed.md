# /retry-failed - Retry Failed Tasks

Move tasks from the failed queue back to incoming for retry.

## Usage

```
/retry-failed              # Interactive - select tasks
/retry-failed --all        # Retry all failed tasks
/retry-failed TASK-abc123  # Retry specific task
```

## What It Does

1. Lists tasks in `.orchestrator/shared/queue/failed/`
2. Moves selected tasks back to `incoming/`
3. Appends `RETRIED_AT` timestamp to task file

## Failed Queue

When a task fails, it's moved to the failed queue with error info:

```markdown
# [TASK-abc123] Add user validation

ROLE: implement
...

FAILED_AT: 2024-01-15T14:30:00
## Error
```
Test suite failed with 3 failures
```
```

## Interactive Mode

```
Failed Tasks
============
1. TASK-abc123 | Add user validation     | failed 2h ago
   Error: Test suite failed

2. TASK-def456 | Update dependencies     | failed 5h ago
   Error: Merge conflict

3. TASK-ghi789 | Fix auth bug            | failed 1d ago
   Error: Claude timeout

Select tasks to retry (comma-separated, or 'all'): 1,2
```

## Retry Strategy

Before retrying, consider:

1. **Was it a transient error?** (timeout, network) - retry likely to succeed
2. **Was it a code issue?** - may need manual intervention first
3. **Is the task still relevant?** - might be outdated

## Implementation

```python
from orchestrator.orchestrator.queue_utils import retry_task, list_tasks

# List failed tasks
failed = list_tasks('failed')
for task in failed:
    print(f"{task['id']}: {task['title']}")

# Retry a specific task
retry_task(task['path'])
```

## Related Commands

- `/queue-status` - See all queues
- `/enqueue` - Create new task
