# Server: Clear claimed_by and lease_expires_at on task submit

## Problem

The `POST /api/v1/tasks/:id/submit` endpoint moves tasks from `claimed` to `provisional` but does not clear `claimed_by` or `lease_expires_at`. This leaves stale claim metadata on the task.

This was a latent bug that became visible when `check_and_evaluate_checks` was added to the scheduler (commit 3817053, 2026-02-28). That function skips provisional tasks where `claimed_by` is set (assuming the gatekeeper is already reviewing them). But if `claimed_by` still says `implementer` from the original claim, the task is silently skipped — checks never run and the gatekeeper never picks it up.

## Fix

In the submit endpoint's UPDATE query, add `claimed_by = NULL, lease_expires_at = NULL`:

```sql
UPDATE tasks
SET queue = ?,
    version = version + 1,
    claimed_by = NULL,
    lease_expires_at = NULL,
    commits_count = ?,
    turns_used = ?,
    check_results = ?,
    execution_notes = ?,
    submitted_at = datetime('now'),
    updated_at = datetime('now')
WHERE id = ? AND queue = 'claimed' AND version = ?
```

## Context

- The claim endpoint correctly sets `claimed_by` when an agent claims a task
- The accept endpoint (done transition) doesn't need to clear it because the task is terminal
- The submit endpoint is the only transition that moves a task to a queue where another agent needs to claim it — so it must clear the previous claim
- This is the same class of bug as the `needs_intervention` leak (postmortem 2026-03-01): state fields that should be cleared on transition but aren't

## Related

- Draft 216: task state should be a state machine with enforced transitions
- Postmortem 2026-03-01-task-868b-intervention-leak: `needs_intervention` not cleared on lease expiry
