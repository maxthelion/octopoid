# /resolve-task - Manually Resolve a Task

Mark a task as manually resolved, bypassing the normal flow transitions.

## When to Use

Use this when work was completed outside the normal agent flow:

- Cherry-picked commits applied directly to the base branch
- Task is no longer needed (superseded, out of scope, cancelled)
- Manual fix applied without going through the agent/review flow
- Failed tasks that are obsolete

This creates a clean audit trail without requiring fake flow gymnastics.

## Usage

```
/resolve-task <task-id> <reason>
```

**Examples:**

```
/resolve-task abc12345 "Cherry-picked from e985ade"
/resolve-task fb8c568c "Superseded by TASK-xyz — new approach taken"
/resolve-task 9580b5ce "No longer needed after architecture change"
```

## Interactive Mode

If called without arguments, I'll ask for:

1. **Task ID** — The 8-character task ID
2. **Resolution note** — Why the task is being resolved manually

## Implementation

Use `resolve_task()` from `orchestrator.tasks`:

```python
from orchestrator.tasks import resolve_task

result = resolve_task(
    task_id="abc12345",
    resolved_by="human",
    resolution_note="Cherry-picked from e985ade — manually applied",
)
print(f"Task {result['id']} resolved (was in queue: {result.get('queue')})")
```

**What this does:**
1. Fetches the task to verify it exists
2. Appends a `## Resolution` section to the task file on disk
3. Calls `sdk.tasks.resolve()` to set `queue=resolved` via the API
4. Logs the resolution in the task history

The task will appear in the Done tab of the dashboard with a `resolved` badge.

## After Resolving

Check the queue status with `/queue-status` to confirm the task is in the resolved queue.
