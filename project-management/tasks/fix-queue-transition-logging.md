# Fix silent queue transition failures: verify API response before logging success

## Problem

In `handle_agent_result()`, the scheduler logs queue transitions (e.g. `submitted (claimed → provisional)`) **before confirming the API call succeeded**. If the server-side update fails (version conflict, network error, etc.), the log says the transition happened but the task stays in its old queue.

This makes debugging very difficult — the logs say everything worked, but the task is stuck.

### Evidence

TASK-proj-seq-cf229d28 second run:
- Scheduler logged: `Task TASK-proj-seq-cf229d28: submitted (claimed → provisional)`
- But the task remained in `claimed` queue on the server
- No error was logged for the failed API call

## Fix

In `handle_agent_result()` and the `_handle_*_outcome()` helpers in `orchestrator/scheduler.py`:

1. **Wrap the API call and check the response** before logging success:

```python
try:
    sdk.tasks.update(task_id, queue="provisional", ...)
    debug_log(f"Task {task_id}: submitted (claimed → provisional)")
except Exception as e:
    debug_log(f"Task {task_id}: FAILED to transition claimed → provisional: {e}")
    # Log to task logger too
    task_logger.log(task_id, "TRANSITION_FAILED",
                    from_queue="claimed", to_queue="provisional", error=str(e))
```

2. **Log the actual API response status**, not just the intended transition.

3. **Handle version conflicts explicitly** — if the task was already moved (e.g. by a previous run's stale result being processed), log that clearly rather than silently failing.

## Acceptance Criteria

- [ ] Queue transition API calls are wrapped in try/except
- [ ] Success is logged only after API confirms the update
- [ ] Failures are logged with the error reason
- [ ] Version conflicts are logged as a specific case (not a generic error)
- [ ] Task logger records TRANSITION_FAILED events
- [ ] Existing tests pass
