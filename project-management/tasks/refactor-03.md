# refactor-03: Extract housekeeping jobs into a list

ROLE: implement
PRIORITY: P1
BRANCH: feature/client-server-architecture
CREATED: 2026-02-15T00:00:00Z
CREATED_BY: human
SKIP_PR: true

## Context

The `run_scheduler()` function (line 1615 of `orchestrator/scheduler.py`) runs 10 independent housekeeping jobs sequentially at the start of every tick before entering the agent evaluation loop. These jobs are called one after another with no error isolation -- if one throws an exception, all subsequent jobs and agent evaluations are skipped for that tick.

This task extracts the housekeeping section into a `run_housekeeping()` function with a `HOUSEKEEPING_JOBS` list. Each job runs in a `try/except` so one failure doesn't kill the tick.

This is a prep step -- we create the function alongside `run_scheduler()` but do NOT modify `run_scheduler()` itself. Task refactor-05 will wire it up.

Reference: `project-management/drafts/10-2026-02-15-scheduler-refactor.md` (Phase 1: Housekeeping)

## What to do

Add the following to `orchestrator/scheduler.py`, placed AFTER the guard functions (from refactor-02) and BEFORE `run_scheduler()`:

### HOUSEKEEPING_JOBS list

```python
HOUSEKEEPING_JOBS = [
    _register_orchestrator,
    check_and_update_finished_agents,
    _check_queue_health_throttled,
    process_orchestrator_hooks,
    process_auto_accept_tasks,
    assign_qa_checks,
    process_gatekeeper_reviews,
    dispatch_gatekeeper_agents,
    check_stale_branches,
    check_branch_freshness,
]
```

These are all existing functions already defined in scheduler.py. Their line numbers for reference:
- `_register_orchestrator` -- line 1593
- `check_and_update_finished_agents` -- line 1214
- `_check_queue_health_throttled` -- line 1481
- `process_orchestrator_hooks` -- line 1132
- `process_auto_accept_tasks` -- line 1179
- `assign_qa_checks` -- line 1116
- `process_gatekeeper_reviews` -- line 1188
- `dispatch_gatekeeper_agents` -- line 1201
- `check_stale_branches` -- line 1418
- `check_branch_freshness` -- line 1346

### run_housekeeping function

```python
def run_housekeeping() -> None:
    """Run all housekeeping jobs. Each is independent and fault-isolated."""
    for job in HOUSEKEEPING_JOBS:
        try:
            job()
        except Exception as e:
            debug_log(f"Housekeeping job {job.__name__} failed: {e}")
```

### Important notes

- The `HOUSEKEEPING_JOBS` list references functions that are defined BELOW it in the file. This is fine because the list is only evaluated at runtime (when `run_housekeeping()` is called), not at module load time. However, if you place the list at module level before the functions are defined, Python will raise a `NameError`. **Solution:** Either place the list after the function definitions, or define it inside `run_housekeeping()`. The cleanest approach is to place both `HOUSEKEEPING_JOBS` and `run_housekeeping()` AFTER all the housekeeping functions but BEFORE `run_scheduler()`.

- Note that `_register_orchestrator` is currently called BEFORE the `is_system_paused()` check in `run_scheduler()`. The new `run_housekeeping()` will be called AFTER the pause check (as shown in the draft). This is intentional -- registration doesn't need to happen when paused. When wiring up in refactor-05, this ordering change is acceptable.

- The `should_trigger_queue_manager()` call at lines 1657-1661 is NOT in the housekeeping list because it's diagnostic-only (logs a message, doesn't trigger anything directly). It can be dropped or kept as a separate call in refactor-05.

## What NOT to do

- Do NOT modify `run_scheduler()` or any existing functions
- Do NOT change tests
- Do NOT change the signatures of any housekeeping functions

## Key files

- `orchestrator/scheduler.py` -- add HOUSEKEEPING_JOBS and run_housekeeping() here
- `project-management/drafts/10-2026-02-15-scheduler-refactor.md` -- design reference

## Acceptance criteria

- [ ] `HOUSEKEEPING_JOBS` list contains references to all 10 functions
- [ ] `run_housekeeping()` iterates the list and calls each job in a `try/except`
- [ ] Failures are logged via `debug_log()` and don't stop subsequent jobs
- [ ] Placement is correct (after the function definitions to avoid `NameError`)
- [ ] No changes to `run_scheduler()` or any existing functions
- [ ] All existing tests pass (`pytest tests/`)
- [ ] Module loads without errors (`python -c "from orchestrator.scheduler import run_housekeeping"`)
