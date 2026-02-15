# Fix stale result.json: clean task dir artifacts between agent runs

## Problem

`result.json` is never cleaned up between agent runs on the same task. When a task is rejected and reclaimed, `prepare_task_directory()` rewrites `env.sh`, `prompt.md`, `task.json`, and `scripts/`, but leaves `result.json` from the previous run in place.

If the new agent run crashes without writing its own `result.json`, the scheduler picks up the **stale one from the previous run** and processes it as if the current run succeeded.

### Evidence

TASK-proj-seq-cf229d28:
1. **11:17** - implementer-2 claimed, ran, wrote `result.json` with `outcome: submitted` at 11:27
2. **11:48** - implementer-1 reclaimed after rejection, ran, crashed (exit code 1)
3. **11:58** - scheduler found the stale `result.json` from run 1, logged `submitted (claimed → provisional)` — wrong

`stdout.log` and `stderr.log` are truncated per run (in the agent dir), but `result.json` (in the task dir) is not touched.

## Fix

In `prepare_task_directory()` (`orchestrator/scheduler.py`), delete stale artifacts before setting up the new run:

```python
# Clean stale artifacts from previous runs
for stale_file in ['result.json', 'notes.md']:
    stale_path = task_dir / stale_file
    if stale_path.exists():
        stale_path.unlink()
        debug_log(f"Cleaned stale {stale_file} from {task_dir}")
```

This should go early in `prepare_task_directory()`, after the task dir is created but before writing new files.

## Acceptance Criteria

- [ ] `prepare_task_directory()` removes `result.json` and `notes.md` from previous runs
- [ ] Cleanup is logged via `debug_log`
- [ ] Existing tests pass
- [ ] Add a unit test: call `prepare_task_directory` twice, verify stale result.json from first call is gone after second call
