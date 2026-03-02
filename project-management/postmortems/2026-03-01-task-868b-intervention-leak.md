# Postmortem: Task 868b0322 — Intervention flag leaks through lease expiry, causing parallel agent chaos

**Date:** 2026-03-01
**Task:** 868b0322 (Build diagnostic agent for failed queue)
**Severity:** Systemic — affects any task that enters intervention and then has its lease expire

## Summary

Task 868b0322 entered a failure loop where an implementer and fixer were running simultaneously on the same task, clobbering each other's stdout.log. The root cause is that `check_and_requeue_expired_leases` moves tasks back to incoming without clearing `needs_intervention`, allowing both the implementer and fixer to claim the same task concurrently.

## Timeline

- **18:00** — Task created via `create_task()`
- **18:07** — Task claimed by implementer (first run, pre-log-rotation)
- **~18:09** — Implementer crashed with empty stdout. Result handler inferred "unknown" outcome, called `request_intervention()`, set `needs_intervention=True` on server
- **~18:10** — Fixer spawned, ran for ~5 minutes, reported "could not fix" (empty stdout again). Task now has `needs_intervention=True` and is back in intervention state
- **~19:07** — Lease expired (60 min from 18:07 claim). `check_and_requeue_expired_leases` moved task to **incoming** queue but did NOT clear `needs_intervention`
- **19:30** — Scheduler restarted (log rotation). Old PIDs lost
- **20:29:36** — Implementer claims task from incoming (guard_claim_task passes because task is in incoming). Spawns PID 72380
- **20:31:49** — Fixer evaluator sees `needs_intervention=True` on server (line 225: `sdk.tasks.list(needs_intervention=True)`). Claims and spawns PID 73627 for the **same task**
- **20:31:49** — Scheduler "cleans stale stdout.log" from task dir — **deleting the implementer's stdout.log while it's still running**
- **20:37:03** — Fixer PID 73627 finishes. Reads (now empty) stdout.log → outcome=failed
- **20:37:06** — Second fixer spawned (PID 75052), same pattern — cleans stdout.log again
- **21:29** — Lease expires again, task requeued to incoming. PID 72380 (implementer) is **still running** (still alive at 22:45)
- **21:32** — Lease expiry detected, task moved to incoming
- **21:51** — Implementer tries to claim but guard_claim_task blocks it ("already being processed")
- **22:42** — We manually force-queued to incoming and cleaned up
- **22:45** — Third fixer finishes, reads missing stdout.log → outcome=unknown → circuit breaker

## Root Cause

**`check_and_requeue_expired_leases` (scheduler.py:1843) does not clear `needs_intervention` when requeuing a task from claimed → incoming.**

The update call is:
```python
sdk.tasks.update(
    task_id,
    queue=target_queue,       # "incoming"
    claimed_by=None,
    lease_expires_at=None,
    attempt_count=new_attempt_count,
)
# needs_intervention is NOT cleared
```

This creates a state where the task is in the **incoming** queue (eligible for implementer claim) but ALSO has `needs_intervention=True` (eligible for fixer claim). Both agents claim the same task concurrently.

## Consequences

1. **Parallel agents on same task** — Implementer and fixer run simultaneously, sharing the same task directory
2. **Stdout clobbering** — The fixer's "clean stale stdout.log" step deletes the implementer's in-progress stdout, causing the implementer to produce empty output
3. **Orphan process** — PID 72380 (implementer) kept running for 2+ hours after the task was moved through intervention and failed, consuming resources and blocking the pool
4. **Fixer false negatives** — Fixer reads empty stdout (clobbered by the cleanup step) and reports "could not fix" — it never saw the implementer's actual output

## Fix

Add `needs_intervention=False` to the lease expiry requeue in `check_and_requeue_expired_leases`:

```python
sdk.tasks.update(
    task_id,
    queue=target_queue,
    claimed_by=None,
    lease_expires_at=None,
    attempt_count=new_attempt_count,
    needs_intervention=False,  # <-- clear the flag
)
```

The reasoning: if a task's lease expired, the agent that set `needs_intervention` is gone. The intervention state is stale. Requeuing to incoming should give it a clean start.

## Secondary Issue: Orphan Process

PID 72380 was still running 2+ hours later. The scheduler lost track of it because:
1. The fixer cleaned the task dir's stdout.log (breaking the implementer's output path)
2. The fixer overwrote running_pids.json for the task dir
3. The implementer PID was never logged as "has finished" — it fell out of the pool tracking

This suggests the pool tracking (running_pids.json) doesn't handle the case where two agents from different blueprints claim the same task simultaneously.

## Affected Invariant

- `circuit-breaker-stops-loops` — the circuit breaker eventually caught this, but only after 3 fixer attempts and 2+ hours of an orphaned process

## Recommendations

1. **Fix the leak** — Add `needs_intervention=False` to lease expiry requeue (one-line fix)
2. **Guard against dual-claim** — `guard_claim_task` for the fixer should check if the task is also currently claimed by a different agent in the pool
3. **Kill orphan on re-claim** — When preparing a task directory for a new agent, check for and kill any existing PID for that task
