# Lease recovery bug: expired leases in provisional not cleared

**Captured:** 2026-02-24

## Raw

> That's a bug in the lease recovery, not a launchd issue. The symptoms: scheduler ticks normally, task has expired lease in provisional, check_and_requeue_expired_leases doesn't clear it.

## Symptoms

- Scheduler is ticking normally (heartbeat is recent)
- A task sits in the `provisional` queue with an expired `lease_expires_at`
- `check_and_requeue_expired_leases` runs every 60s but doesn't clear the stale claim
- The task is stuck — no gatekeeper picks it up because it appears claimed

## Code path

`scheduler.py:1740` — `check_and_requeue_expired_leases()`:

1. Lists tasks in `claimed` and `provisional` queues
2. For provisional: skips tasks where `claimed_by` is not set (line 1768)
3. Checks `lease_expires_at` — skips if not set (line 1772)
4. If expired, calls `sdk.tasks.update(task_id, queue="provisional", claimed_by=None, lease_expires_at=None)` (line 1815)

### Possible failure points

1. **`claimed_by` is already None but `lease_expires_at` is still set.** The guard at line 1768 skips tasks where `claimed_by` is falsy. If the gatekeeper process died and `check_and_update_finished_agents` already cleared `claimed_by` but didn't clear `lease_expires_at`, the task would have a stale lease but no `claimed_by` — and this function would skip it.

2. **Server rejects the update.** The update sets `queue="provisional"` when the task is already in provisional. If the server's state machine rejects same-queue transitions, the update would silently fail (the exception is caught at line 1819-1820).

3. **`lease_expires_at` format mismatch.** The datetime parsing at line 1776 uses `fromisoformat()`. If the server returns a non-ISO format or a format that Python's `fromisoformat()` can't parse, the `ValueError` is caught and silently skipped.

4. **The task list response doesn't include `lease_expires_at`.** If the server omits the field from the list response (only includes it on individual GET), the function would see `None` at line 1772 and skip.

## Context

Observed while investigating agent turn usage. A task was stuck in provisional with an expired lease, and the scheduler wasn't clearing it despite ticking normally. This blocks the gatekeeper from picking up the task for review.

## Invariants

- `expired-lease-cleared`: Tasks with expired leases in any queue are detected and their lease state is cleared by the scheduler's housekeeping job within one housekeeping cycle. A task cannot remain indefinitely blocked by a stale `lease_expires_at` value — lease recovery runs on a regular interval and must handle tasks regardless of whether `claimed_by` is set.

## Open Questions

- Which failure point is the actual cause? Need to reproduce and check scheduler debug logs.
- Does the server return `lease_expires_at` in the task list response, or only on individual task GET?
- Does the server accept `sdk.tasks.update(queue="provisional")` when the task is already in provisional?
- Should `check_and_update_finished_agents` be responsible for clearing `lease_expires_at` when it clears `claimed_by`?

## Possible Next Steps

- Check scheduler debug logs for the specific task to see if the function is being called and what it sees
- Add logging at each branch point in the function (before the `claimed_by` guard, after the lease check, after the update call)
- Test whether `sdk.tasks.update(queue="provisional", ...)` succeeds when task is already provisional
- Consider clearing `lease_expires_at` in `check_and_update_finished_agents` alongside `claimed_by`
