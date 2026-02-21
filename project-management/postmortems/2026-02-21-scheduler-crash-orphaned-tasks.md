# Scheduler crash → orphaned tasks in requeue loop

**Date:** 2026-02-21
**Duration:** ~12 hours (20:39 Feb 20 → 08:09 Feb 21)
**Impact:** 3 tasks stuck in claim/requeue loop all night, wasted agent compute
**Related:** Agents writing to main tree (separate root cause, same session)

## Symptoms

- Dashboard showed 3 tasks in RUN state with 0/100t for hours
- `/queue-status` showed "Last tick: 16h ago"
- `running_pids.json` was empty despite tasks being in `claimed` queue
- Tasks kept appearing in `claimed` with fresh `lease_expires_at` timestamps

## Timeline

1. **~21:00** — Interactive session runs `git pull --recurse-submodules`, hits merge conflicts in `scheduler.py`
2. **~21:03** — Conflicts resolved, but `__pycache__` still has old bytecode
3. **~22:00** — Scheduler spawns agents for tasks c50c2d63, 72116952, 401995d0
4. **~22:27** — Agents finish (result.json: outcome=done), PIDs die
5. **~22:30** — `__pycache__` expires or launchd restarts, scheduler imports `scheduler.py` with merge conflict markers still present from a stash pop
6. **22:30+** — `SyntaxError: unmatched ')'` and `IndentationError: expected an indented block` in launchd-stderr.log
7. **~23:47** — Some ticks succeed (partial import?), expired lease requeue fires: "Requeued expired lease: 401995d0 → incoming"
8. **23:52** — One task (72116952) briefly gets processed: "transitioned to provisional via flow"
9. **00:54** — Same task requeued again by expired lease check: "Requeued expired lease: 72116952 → incoming"
10. **All night** — Tasks cycle: incoming → claimed → lease expires → incoming → claimed → ...
11. **07:58** — Last scheduler tick in logs, then launchd throttles the job
12. **08:09** — Manual intervention: process results, reload launchd

## Root cause

**Merge conflict markers left in `scheduler.py` after interactive rebase.**

The rebase resolved conflicts in the editor, but a separate `git stash pop` (triggered by submodule sync) reintroduced conflict markers. The scheduler's `--once` mode via launchd meant each tick was a fresh Python import. When `__pycache__` was stale, the old bytecode ran fine. When cache was cleared or invalidated, Python hit the syntax error.

The scheduler has two independent systems that both touch claimed tasks:
- `check_and_update_finished_agents()` — processes results for dead PIDs
- `check_and_requeue_expired_leases()` — requeues tasks with expired leases

When result processing failed (syntax error in import), the lease expiry check still ran and kept requeuing the tasks. This created an infinite loop: claim → agent runs → agent finishes → result not collected → lease expires → requeue → reclaim.

## Why PIDs disappeared

Each reclaim cycle overwrites `running_pids.json` with the new PID. When the new agent finishes and dies, the scheduler can't process it (syntax error), so the PID entry just sits there until the next reclaim overwrites it. Eventually all PIDs are gone.

## Fixes applied

1. Resolved merge conflict markers in `scheduler.py`
2. Cleared `__pycache__`
3. Manually processed 3 orphaned tasks via `handle_agent_result()`
4. Reloaded launchd plist (`unload` + `load`) to un-throttle

## Fixes applied (related issues found during investigation)

5. **Turn counter**: Added PostToolUse hook to worktree `.claude/settings.json` at spawn time (the reader from PR #125 was merged but the writer was never built)
6. **Agent file isolation**: Added project-relative permissions (`Edit(/**)` instead of `Edit(**)`) to worktree settings, preventing agents from writing to main tree via absolute paths

## Lessons

- **Always verify `scheduler.py` compiles after any rebase/merge.** Run `python3 -c "import py_compile; py_compile.compile('orchestrator/scheduler.py', doraise=True)"` after resolving conflicts.
- **The lease expiry requeue and result collection are not atomic.** If result collection fails, lease expiry creates infinite loops. These systems need coordination — e.g. lease expiry should check if `result.json` exists before requeuing.
- **launchd throttles jobs that crash repeatedly.** After fixing a crash, always `unload` + `load` the plist to reset the throttle.
- **Stale `__pycache__` masks syntax errors.** The scheduler can appear to run fine for hours with broken source code.
