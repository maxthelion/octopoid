# Fix silent queue transition failures: verify API response before logging success

## Problem

In `orchestrator/scheduler.py`, three functions call `sdk.tasks.update()` to transition tasks between queues but don't check if the API call succeeded. If the server returns an error, the code logs success anyway.

## IMPORTANT: What to change

You MUST change **only** these three functions in `orchestrator/scheduler.py`. Do NOT modify `queue_utils.py`, `migrate.py`, `db.py`, or any other file besides scheduler.py and a test file.

The bug is in the **SDK-based API calls** (`sdk.tasks.update()` and `sdk.tasks.submit()`), NOT in the old DB-based code (`db.update_task_queue()`). Do not touch any `db.*` calls.

### Function 1: `_handle_submit_outcome()` (around line 957)

The fallback at line 971 is unwrapped:

```python
# CURRENT (line 969-972):
        except Exception as e:
            # Fallback: if submit fails, manually move to provisional
            debug_log(f"Task {task_id}: submit failed ({e}), falling back to provisional update")
            sdk.tasks.update(task_id, queue="provisional")
            _update_pr_metadata(sdk, task_id, result)
```

Change to:
```python
        except Exception as e:
            debug_log(f"Task {task_id}: submit failed ({e}), falling back to provisional update")
            try:
                sdk.tasks.update(task_id, queue="provisional")
                _update_pr_metadata(sdk, task_id, result)
            except Exception as e2:
                debug_log(f"Task {task_id}: TRANSITION FAILED claimed → provisional: {e2}")
```

### Function 2: `_handle_fail_outcome()` (around line 1002)

```python
# CURRENT (line 1003-1005):
    if current_queue == "claimed":
        # Normal case — update to failed
        sdk.tasks.update(task_id, queue="failed")
        debug_log(f"Task {task_id}: failed (claimed → failed): {reason}")
```

Change to:
```python
    if current_queue == "claimed":
        try:
            sdk.tasks.update(task_id, queue="failed")
            debug_log(f"Task {task_id}: failed (claimed → failed): {reason}")
        except Exception as e:
            debug_log(f"Task {task_id}: TRANSITION FAILED claimed → failed: {e}")
```

### Function 3: `_handle_continuation_outcome()` (around line 1033)

```python
# CURRENT (line 1033-1036):
    if current_queue == "claimed":
        # Normal case — update to needs_continuation
        sdk.tasks.update(task_id, queue="needs_continuation")
        debug_log(f"Task {task_id}: needs continuation (claimed → needs_continuation) by {agent_name}")
```

Change to:
```python
    if current_queue == "claimed":
        try:
            sdk.tasks.update(task_id, queue="needs_continuation")
            debug_log(f"Task {task_id}: needs continuation (claimed → needs_continuation) by {agent_name}")
        except Exception as e:
            debug_log(f"Task {task_id}: TRANSITION FAILED claimed → needs_continuation: {e}")
```

## Do NOT change

- Do NOT modify `orchestrator/queue_utils.py`
- Do NOT modify `orchestrator/migrate.py`
- Do NOT modify `orchestrator/db.py` or any `db.*` calls
- Do NOT modify `tests/test_db.py`
- Do NOT create `tests/test_queue_transition_verification.py`
- Do NOT add CHANGELOG entries
- Only modify `orchestrator/scheduler.py` and optionally add a test in `tests/test_scheduler.py`

## Acceptance criteria

- [ ] `_handle_fail_outcome()` wraps `sdk.tasks.update()` in try/except
- [ ] `_handle_continuation_outcome()` wraps `sdk.tasks.update()` in try/except
- [ ] `_handle_submit_outcome()` fallback wraps `sdk.tasks.update()` in try/except
- [ ] All three log `TRANSITION FAILED` on error with the exception message
- [ ] Success is only logged after the API call succeeds (inside the try, not after)
- [ ] No changes to queue_utils.py, migrate.py, db.py, or any db.* calls
- [ ] Existing tests pass
