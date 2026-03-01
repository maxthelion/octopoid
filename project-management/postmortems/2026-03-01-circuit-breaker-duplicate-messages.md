# Circuit Breaker Fires Multiple Times Per Task

**Date:** 2026-03-01
**Task affected:** e18ce3e4 (Restructure system-spec.yaml into hierarchical directory tree)
**Symptom:** ~10 duplicate `circuit_breaker` messages posted to the same task
**Root cause:** Guard function doesn't check whether it already fired the circuit breaker for this task. Each scheduler tick re-evaluates, finds the task still matches, and fires again.

## Timeline

1. Task e18ce3e4 completes successfully — PR #281 merged, all steps pass
2. Task fails to transition to `done` on the server (state transition error)
3. Fixer loop begins: fixer reports "fixed," resume fails, intervention re-requested
4. After 3 `intervention_reply` messages, circuit breaker fires in `guard_claim_task` (line 247)
5. Circuit breaker does two things:
   - `sdk.tasks.update(queue="failed", needs_intervention=False)` — moves task to failed
   - `sdk.messages.create(type="circuit_breaker")` — posts notification
6. On next scheduler tick (60s later), `guard_claim_task` runs again
7. Query: `sdk.tasks.list(needs_intervention=True)` — task no longer matches (needs_intervention=False)
   **OR** the task is in `failed` queue and still has `needs_intervention=True` due to a race
8. If the task still appears in the list, the guard re-counts `intervention_reply` messages
9. Count is still >= 3, so circuit breaker fires again — duplicate message posted
10. This repeats every tick until the task stops appearing in the query

## Why It Fires Multiple Times

The guard at line 247 only checks `intervention_reply` message count. It doesn't check:
- Whether a `circuit_breaker` message already exists for this task
- Whether the task is already in the `failed` queue
- Whether it already processed this task in a previous tick

The `sdk.tasks.update(queue="failed", needs_intervention=False)` call at line 252 should prevent the task from appearing in the `needs_intervention=True` query on subsequent ticks. But there are two scenarios where it still fires:

1. **Race condition:** The update hasn't propagated by the time the next tick's query runs (unlikely with synchronous HTTP, but possible with server-side caching or eventual consistency)
2. **Update failure:** The `sdk.tasks.update` call silently fails (e.g. 409 conflict because the task's queue was already changed by another process), but execution continues to the `sdk.messages.create` call and the `continue` statement. Next tick, the task is still in intervention state.

Scenario 2 is the more likely cause — if the task can't transition from its current queue to `failed` (invalid transition), the update throws but is not in a try/except, so the exception propagates... except it IS caught by the broad `except Exception` at line 267, which just logs a debug message and moves on. Wait — no, the try/except at line 267 only wraps the message-fetching code, not the update+message posting at lines 252-265.

Looking more carefully: the `sdk.tasks.update` at line 252 is inside the `try` block that starts at line 241. If the update fails, the exception is caught at line 267 (`except Exception as msg_e`), logged as debug, and the loop continues to the next candidate. But crucially, the task is NOT marked as processed — so next tick, it appears again, the messages are fetched successfully, the count is >= 3, and the circuit breaker code runs again. If the update fails again but the message creation succeeds (or vice versa), we get duplicate messages without the task ever moving to failed.

**The real bug:** The circuit breaker's "move to failed" and "post notification" are not atomic, and failures in either are swallowed by the broad except clause. The guard has no memory between ticks — it re-evaluates from scratch each time.

## The Fix

Two changes needed:

### 1. Check for existing circuit_breaker messages before firing

Before firing the circuit breaker, check if a `circuit_breaker` message already exists for this task. If so, skip — we already handled it (or tried to).

```python
circuit_breaker_msgs = [
    m for m in msgs.get("messages", [])
    if m.get("type") == "circuit_breaker"
]
if circuit_breaker_msgs:
    continue  # Already fired, skip
```

### 2. Check task queue before firing

If the task is already in `failed`, don't fire the circuit breaker again:

```python
if candidate.get("queue") == "failed":
    continue
```

### 3. Separate error handling for the update vs message creation

The `sdk.tasks.update` and `sdk.messages.create` calls should have their own try/except so a failure in one doesn't prevent or duplicate the other, and doesn't silently fall through to retry on next tick.

## Relationship to Other Postmortems

This is the circuit breaker that was added to fix the fixer loop documented in `2026-02-28-fixer-loop-stripped-transitions.md`. The circuit breaker correctly prevents infinite fixer loops but has its own bug — it fires repeatedly because it has no memory of having already fired.
