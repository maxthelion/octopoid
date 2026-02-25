# Unblock dependent tasks when a task moves to failed

## Problem

When a task is accepted (POST /tasks/:id/accept), the server unblocks dependent tasks by clearing `blocked_by` references to the completed task (lines 852-857 in tasks.ts).

But when a task moves to `failed`, **no unblocking happens**. This means:
- If task A blocks task B, and task A fails, task B stays blocked forever
- The only fix is manual intervention: a human must clear B's `blocked_by` field
- This creates invisible stuck tasks that sit in the queue indefinitely

## Proposed fix

Add the same unblocking logic to the PATCH endpoint when `queue` changes to `failed`. Currently PATCH already handles claim clearing on fail (commit f228964). Add unblocking in the same place:

```typescript
// In PATCH /api/v1/tasks/:id, after detecting queue changed to 'failed':
if (updates.queue === 'failed') {
    // Clear claim (already done per f228964)
    // ...

    // Unblock dependents — a failed task will never complete,
    // so holding up dependent work is worse than releasing it
    await execute(db,
        `UPDATE tasks
         SET blocked_by = NULL, updated_at = datetime('now')
         WHERE blocked_by = ?`,
        taskId
    )
}
```

## Rationale

A failed task will never transition to done (without being requeued first, which gives it a new lifecycle). Keeping dependents blocked on a failed task serves no purpose — it just creates invisible stuck work. Unblocking lets the dependent tasks proceed; if the failed task's work was actually needed, the dependent task will fail on its own merits.

This matches the architectural principle: "failed should be an outlier outcome" — when it does happen, the system should self-recover as much as possible rather than requiring human intervention.

## Acceptance criteria

- [ ] When a task's queue changes to `failed` (via PATCH), all tasks with `blocked_by` pointing to it have their `blocked_by` cleared
- [ ] Existing accept endpoint unblocking continues to work unchanged
- [ ] Add a test: create task A blocking task B, fail task A, verify B's blocked_by is cleared
