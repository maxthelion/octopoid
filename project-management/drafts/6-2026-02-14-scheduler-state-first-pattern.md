# Simplify Scheduler Result Handling with State-First Pattern

**Status:** Idea
**Captured:** 2026-02-14

## Raw

> `handle_agent_result()` keeps getting more complex with defensive error handling. The latest addition was a force-update fallback for expired leases. Look at the scheduler holistically — is there a better abstraction?

## Idea

Replace the "try transition, catch error, force-update" pattern in `handle_agent_result()` with a "check state first, act accordingly" pattern. Fetch the task's current server state before deciding what to do. Handle each state/outcome combination explicitly instead of catching exceptions.

## Context

**Root cause of stuck tasks:** The server default lease is 5 minutes, but agents run 5-30 minutes. When the lease expires, `sdk.tasks.submit()` fails with 409, and the catch-all error handler tries `fail_task()` which also fails. Task stuck in `claimed` forever.

**The server already handles recovery:** A lease monitor auto-requeues expired leases every minute. And the `submit-pr` script already calls the server's submit endpoint before the agent exits. So by the time `handle_agent_result()` runs, the task may already be `provisional` (from submit-pr) or `incoming` (from lease monitor). The current code doesn't check — it blindly tries `sdk.tasks.submit()` and catches the 409.

**Existing bugs found:**
- `fail_task(task_id, reason=reason)` — wrong args, function expects `(task_path, error)`
- `mark_needs_continuation(task_id, agent_name=agent_name)` — missing required `reason` parameter

## Design

### 1. Keep 1-hour lease in `claim_task()` (already done)

`orchestrator/queue_utils.py` line 466 — `lease_duration_seconds=3600`

### 2. Rewrite `handle_agent_result()` in `orchestrator/scheduler.py`

Replace lines 891-1002. New pattern:

1. Read result.json (same as now)
2. **Fetch current task state** from server via `sdk.tasks.get()`
3. Switch on `current_queue` × `outcome`:

| current_queue | outcome=submitted | outcome=failed | outcome=needs_continuation |
|---|---|---|---|
| `claimed` | Normal: `sdk.tasks.submit()` | `sdk.tasks.update(queue="failed")` | `sdk.tasks.update(queue="needs_continuation")` |
| `provisional` | Already done (submit-pr did it), update PR metadata | Keep it — submit-pr succeeded | Skip — already submitted |
| `incoming` | Lease monitor requeued — log, save PR metadata | Already requeued, will be retried | Already requeued, will be retried |
| `done`/`failed` | Terminal, skip | Terminal, skip | Terminal, skip |

### Key design decisions

- **No calls to `queue_utils.fail_task()` or `mark_needs_continuation()`** — these expect file paths and do markdown file I/O. Use `sdk.tasks.update()` directly for queue changes. Fixes the argument mismatch bugs.
- **One narrow fallback:** If task is `claimed` and `sdk.tasks.submit()` fails (tiny race window between state check and submit), fall back to `sdk.tasks.update(queue="provisional")`.
- **Idempotent:** Safe to call multiple times. If task already in expected state, just update metadata.

### Helper functions to extract

- `_handle_submit_outcome(sdk, task_id, task_dir, result, current_queue)`
- `_handle_fail_outcome(sdk, task_id, reason, current_queue)`
- `_handle_continuation_outcome(sdk, task_id, agent_name, current_queue)`
- `_count_commits(task_dir, result)` — from inline code
- `_update_pr_metadata(sdk, task_id, result)` — from inline code

### What stays the same

- `submit-pr` script's server call (belt-and-suspenders)
- `check_and_update_finished_agents()` — only caller, no changes needed
- Server lease monitor — handles recovery for us
- `queue_utils.fail_task()` / `mark_needs_continuation()` — other callers use them correctly

## Files to Modify

- `orchestrator/scheduler.py` — rewrite `handle_agent_result()` (lines 891-1002) + add helper functions
- `orchestrator/queue_utils.py` — already has `lease_duration_seconds=3600` fix

## Verification

1. Run scheduler with `--once --debug`, verify agent can complete a task end-to-end
2. Check debug logs show state-first pattern ("already provisional, updating metadata only")
3. Verify no TypeErrors from fixed argument mismatches
