# Orphaned Claimed Tasks in Pure-Function Model

**Status:** Analysed
**Captured:** 2026-02-19

## Raw

> We're getting a lot of orphans in claimed recently. I think it's a result of shifting to the pure function model of implementers. It feels like they're not being cleaned up properly. Do a deep dive on this and come up with solutions

## Problem

Since moving implementers to the pure-function/flow-driven model, tasks are frequently getting stuck in "claimed" with no agent process running (no-pid). Observed on 2026-02-19: TASK-e37bc845, 6827deda, and 8444e6ed all stuck in claimed with no-pid. Requeuing 6827deda didn't stick — the scheduler re-claimed it immediately and the agent went no-pid again.

## Root Causes (6 identified)

### 1. `guard_not_running` state corruption (CRITICAL)

`scheduler.py:86-91` — when the guard sees `state.running == True`, it calls `mark_finished()` and proceeds, **without checking whether the PID is still alive first**. The full logic:

```python
if ctx.state.running:
    ctx.state = mark_finished(ctx.state, 1)  # marks finished unconditionally!
    save_state(ctx.state, ctx.state_path)
return (True, "")
```

This can mark a perfectly healthy running agent as "finished" if it happens to check at the wrong moment. Evidence: implementer-1 has `running=true` with PID 43472 (alive), but also `last_exit_code=1` and `consecutive_failures=27` — contradictory state from repeated false "finished" signals.

**Fix:** Check `is_process_running(ctx.state.pid)` before calling `mark_finished()`.

### 2. Spawn failure after claim — task never requeued

In `spawn_implementer()` (lines 1546-1557), the sequence is:
1. `prepare_task_directory()` — heavy I/O (worktree creation, template rendering)
2. `invoke_claude()` — subprocess.Popen
3. `save_state()` — writes PID to state.json

If step 1 or 2 fails, the exception propagates to the catch block at line 1689-1693 which calls `_requeue_task()`. But if `_requeue_task()` itself fails (server timeout, etc.), or the scheduler crashes between claim and requeue, the task is orphaned forever.

Evidence: implementer-2 has `total_runs=68, total_successes=0` — but see root cause 6 below for why this stat is misleading.

### 3. No lease expiry sweep on orchestrator side

The server has a lease-monitor (`scheduled/lease-monitor.ts`) that requeues expired leases every minute. But the orchestrator has **no equivalent fallback**. If the server lease-monitor is down or the server is unreachable, expired leases accumulate.

The `HOUSEKEEPING_JOBS` list has no lease-expiry checker:
```python
HOUSEKEEPING_JOBS = [
    _register_orchestrator,
    check_and_update_finished_agents,
    _check_queue_health_throttled,
    process_orchestrator_hooks,
]
```

### 4. `check_and_update_finished_agents` has blind spots

The cleanup function (lines 1277-1323) iterates agent state directories and checks for dead PIDs. It misses:
- **Tasks with no state.json** — if spawn failed before state was written, the agent is invisible to cleanup
- **Tasks claimed by deleted/renamed agents** — cleanup only checks agents that have state directories
- **Tasks where state.running=false but task is still claimed on server** — no reconciliation between local state and server state

### 5. State written too late in spawn path

`spawn_implementer()` writes state.json **after** both `prepare_task_directory()` and `invoke_claude()` succeed. If the process starts (step 2) but `save_state()` fails (step 3), the process is running but invisible to cleanup — no PID recorded, so `check_and_update_finished_agents` will never find it.

### 6. `total_successes` is always 0 — exit code never written by pure-function agents

The `total_successes` / `total_failures` stats in state.json are **completely wrong** for pure-function implementers. Here's why:

When `check_and_update_finished_agents` detects a dead PID, it calls:
```python
exit_code = read_agent_exit_code(agent_name)  # reads .octopoid/runtime/agents/<name>/exit_code
if exit_code is None:
    exit_code = 1  # assume crashed!
```

The `exit_code` file is written by `base.py:_write_exit_code()` — part of the **Python role system** (gatekeeper uses this via `BaseRole.execute()`). But pure-function implementers are spawned via `invoke_claude()` which does `subprocess.Popen(["claude", ...])` — a detached process that **never writes an exit_code file**.

So the flow is:
1. `claude` process finishes successfully (exits 0)
2. Scheduler detects dead PID
3. `read_agent_exit_code()` returns `None` (no file exists)
4. Scheduler assumes `exit_code = 1` (crash)
5. `mark_finished(state, 1)` increments `total_failures` and `consecutive_failures`

**Every single pure-function agent completion is counted as a failure.** The `total_successes=0` on both implementers is not evidence of actual failures — it's a broken metric. The agent may have completed its work fine but the scheduler never knows.

This also means `consecutive_failures` is meaningless for implementers — it just counts "number of times the agent has run", not actual failures. Any guard logic that uses `consecutive_failures` for backoff will incorrectly throttle healthy agents.

**Fix:** Either:
- (a) Have `invoke_claude` wrap the subprocess in a shell script that writes the exit code on completion
- (b) Read the claude process's actual exit code via `os.waitpid()` or a wrapper that polls
- (c) Use `result.json` as the success signal instead of exit code — if result.json exists with `status: success`, count it as a success in `mark_finished`

Option (c) is most aligned with the pure-function model — the result.json is already the canonical output.

## Impact

The pure-function model amplifies all of these because:
- Each task claim involves heavy I/O (worktree setup) that can fail
- The agent process is short-lived (one task, then exit) so there are more spawn cycles
- More spawn cycles = more chances for the claim-spawn gap to cause orphans

## Fixes (ranked by priority)

### Fix 1: Guard logic — check PID before marking finished
**File:** `scheduler.py:86-91`
**Effort:** Small
**Impact:** Prevents state corruption on healthy agents

### Fix 2: Add lease expiry housekeeping job
**File:** `scheduler.py` — new function in HOUSEKEEPING_JOBS
**Effort:** Small
**Impact:** Safety net for all orphan types — if nothing else catches it, expired leases get requeued

### Fix 3: Better spawn failure recovery
**File:** `scheduler.py:1686-1693`
**Effort:** Small
**Impact:** Adds full traceback logging on spawn failure, logs requeue success/failure, catches requeue errors

### Fix 4: Write state earlier in spawn path
**File:** `scheduler.py:1546-1557`
**Effort:** Medium (needs refactoring of prepare_task_directory/invoke_claude ordering)
**Impact:** Eliminates the "running but invisible" blind spot

### Fix 5: Periodic orphan scan
**File:** `scheduler.py` — new housekeeping job
**Effort:** Medium
**Impact:** Catches all orphan types by comparing server claimed tasks against local running agents

### Fix 6: Use result.json for success/failure stats, not exit code
**File:** `scheduler.py` — `check_and_update_finished_agents`
**Effort:** Small
**Impact:** Fixes broken metrics. `total_successes`/`total_failures` become meaningful again.

In `check_and_update_finished_agents`, after detecting a dead PID:
1. Check if `result.json` exists in the task dir (from `state.extra["task_dir"]`)
2. If it exists and `status == "success"`, use exit_code 0
3. Fall back to `read_agent_exit_code()` for agents that use the Python role system
4. Only default to exit_code 1 if neither signal exists

## Testing with integration tests

These fixes need proper integration tests against a real local server. Per CLAUDE.md testing philosophy: end-to-end > integration > unit.

### What to test

**Spawn lifecycle tests** (real server, mock agent process):
- Task claimed → agent spawns → agent exits 0 → task moves to provisional → `total_successes` increments
- Task claimed → agent spawns → agent exits 1 → task requeued to incoming → `total_failures` increments
- Task claimed → spawn fails (Popen raises) → task requeued to incoming → no orphan
- Task claimed → spawn fails → requeue fails → lease expires → server lease-monitor requeues

**Guard tests:**
- Agent running (PID alive) → `guard_not_running` blocks → no state corruption
- Agent dead (PID gone) → `guard_not_running` marks finished → state is correct
- Agent never started (no PID in state) → guard proceeds without calling `mark_finished`

**Orphan detection tests:**
- Task in claimed with expired lease → housekeeping requeues it
- Task in claimed with no corresponding agent state → detected and requeued
- Task in claimed with dead PID but no exit_code file → result.json used instead

### How to test

Use the `scoped_sdk` fixture from `tests/integration/conftest.py` for isolation. Mock the agent process with a script that:
1. Writes a `result.json` with configurable status
2. Optionally writes an `exit_code` file
3. Exits with a configurable code

This avoids running real `claude` invocations while testing the full scheduler pipeline. The mock agent script can live at `tests/integration/fixtures/mock-agent.sh`.

The scheduler functions under test (`check_and_update_finished_agents`, `guard_not_running`, `spawn_implementer`) should be callable directly in tests — they're pure functions of state + config + SDK, which makes them testable without running the full scheduler loop.

### Test file structure

```
tests/integration/
  test_scheduler_lifecycle.py    # spawn → finish → cleanup
  test_scheduler_guards.py       # guard logic edge cases
  test_scheduler_orphans.py      # orphan detection and recovery
  fixtures/
    mock-agent.sh                # configurable mock agent
```

Note: there are already tasks in incoming for some of these tests (`914b1e2a` — mock agent fixtures, `98f9d8ef` — scheduler lifecycle tests, `fd0736c2` — edge case tests). Coordinate with those rather than duplicating.

## Related

- Draft 37: Atomic claim transactions (server-side data consistency — different failure mode: `claimed_by=NULL`)
- Draft 39: Independent tick intervals (lease expiry check could be a fast-tick local job)
