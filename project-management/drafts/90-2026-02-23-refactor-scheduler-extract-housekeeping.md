# Refactor scheduler.py: extract housekeeping jobs into housekeeping.py

**Author:** codebase-analyst
**Captured:** 2026-02-23

## Analysis

`orchestrator/scheduler.py` has grown to **2,398 lines** and contains at least three distinct
concerns bundled together:

1. **Agent evaluation / spawning** — the filter chain (`guard_*` functions, `evaluate_agent`),
   spawn preparation (`prepare_task_directory`, `invoke_claude`, `prepare_job_directory`), and
   the main tick loop (`_run_agent_evaluation_loop`, `run_scheduler`).

2. **Background housekeeping jobs** — a suite of maintenance functions (~1,000 lines) that run
   on a schedule to keep the system clean and consistent.

3. **Utility helpers** — scheduler state persistence, lock paths, env-file writing, etc.

The housekeeping jobs are the clearest extraction target: they share no internal state with the
spawning logic, they are already invoked through a single `run_housekeeping()` entry point, and
they each operate at arm's length via the SDK.

## Proposed Split

**New module: `orchestrator/housekeeping.py`**

Move the following functions from `scheduler.py` into the new module:

| Function | Line in scheduler.py | Responsibility |
|---|---|---|
| `process_orchestrator_hooks` | 1171 | Run merge_pr / other orchestrator hooks on provisional tasks |
| `check_and_update_finished_agents` | 1322 | Reap finished agent processes, call `handle_agent_result` |
| `_check_queue_health_throttled` | 1422 | Throttled wrapper for queue health |
| `check_queue_health` | 1441 | Detect and re-queue stale tasks |
| `_evaluate_project_script_condition` | 1534 | Evaluate a flow script condition for project transitions |
| `_execute_project_flow_transition` | 1596 | Execute a single project flow transition |
| `check_project_completion` | 1677 | Scan projects for completion/flow transitions |
| `check_and_requeue_expired_leases` | 1727 | Return lease-expired tasks to incoming |
| `_register_orchestrator` | 1812 | Register / re-register orchestrator with server |
| `send_heartbeat` | 1885 | Send heartbeat ping to server |
| `sweep_stale_resources` | 1901 | Archive logs and delete worktrees for old tasks |
| `run_housekeeping` | 2032 | Orchestrate all of the above on a schedule |

`scheduler.py` would call `from .housekeeping import run_housekeeping` and nothing else from that
set. Estimated reduction: **~1,000 lines** from scheduler.py (down to ~1,400 lines).

## Complexity

**Medium.** The functions themselves don't need to change — only their location. Key points:

- `check_and_update_finished_agents` calls `handle_agent_result` / `handle_agent_result_via_flow`
  from `result_handler.py` — the import will move to `housekeeping.py` (no circular risk).
- Several functions use `debug_log()` — this helper will need to be imported or duplicated
  (prefer importing from a shared `scheduler_debug.py` or inlining a simple `print`-based fallback).
- `run_housekeeping` calls `is_job_due` / `record_job_run` from scheduler state utilities — those
  either stay in `scheduler.py` (and get imported by `housekeeping.py`) or move to a shared
  `scheduler_state.py`.
- No external callers import these functions directly (they're internal to the scheduler process),
  so there are no import-site changes outside the `orchestrator/` package.
- Existing tests in `tests/test_scheduler_refactor.py` and related files will need import-path
  updates if they reference these functions directly.
