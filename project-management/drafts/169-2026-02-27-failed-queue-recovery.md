# Failed queue recovery: reduce failures and make recovery trivial

**Status:** Idea
**Captured:** 2026-02-27

## Raw

> We keep running into this issue. 1. Failures shouldn't be normal for us. 2. If they do happen, we should be able to fix them easily.

The specific trigger: a task with a merged PR and completed work is stuck in `failed` with no way to move it to `done`. The server blocks `queue='done'` via PATCH (must use `/accept`), and `/accept` requires a valid flow transition from the current queue — but no `failed -> done` transition exists. Every time this happens, we have to hack around it: PATCH to `incoming`, PATCH to `claimed`, PATCH to `provisional`, then call `/accept`. Sometimes even that doesn't work because the accept endpoint checks whether a gatekeeper actually claimed it.

## Two problems

### Problem 1: Tasks reach `failed` that shouldn't

Tasks end up in `failed` when the work is actually done:

| Cause | Example | Status |
|-------|---------|--------|
| `update_changelog` throws after `merge_pr` already accepted | Task 2a06729d — PR merged, task accepted to done, then catch-all overwrites done with failed | **Fixed** (non-fatal catch) |
| Step execution fails on a non-critical post-merge step | Any step after `merge_pr` in the `runs` list | Partially fixed (only update_changelog) |
| Lease expires before scheduler processes result | Task 543cd9d7 — scheduler was down for 5h, lease expired, submit got 409 | **Fixed** (plist rename) |
| Agent process dies mid-run | OOM, Claude API timeout, etc. | No mitigation |

The `fail_task()` unification (task 543cd9d7) improved logging for failures, but didn't reduce the number of paths that lead to failure.

**Key insight:** The `merge_pr` step calls `sdk.tasks.accept()` which moves the task to `done`. Any step that runs _after_ merge_pr in the same `runs` list can throw and hit the catch-all exception handler, which then overwrites `done` with `failed`. The task is done but the server says it's failed.

**Possible fixes:**
- **Split post-merge steps into a separate, non-fatal phase.** After `merge_pr` succeeds and the task is accepted, subsequent steps (update_changelog, cleanup) run in a try/except that logs warnings but never touches the task queue. The current fix only covers `update_changelog` — any new post-merge step would have the same bug.
- **Make the catch-all check whether the task is already in `done` before overwriting.** If `sdk.tasks.get(task_id)['queue'] == 'done'`, don't call `fail_task()`.
- **Register `done` as a terminal state that `fail_task()` refuses to overwrite.** `fail_task()` checks current queue and raises/warns if the task is already done.

### Problem 2: No way to recover from `failed` to `done`

Once a task is in `failed`, there's no clean path to `done`:

- `PATCH queue='done'` — server returns 400 (explicitly blocked)
- `POST /accept` — server returns 400 (no `failed -> done` flow transition)
- Walk through intermediate queues — fragile, requires claiming, sometimes fails

**Possible fixes:**
- **Add a `/recover` or `/force-done` admin endpoint on the server.** Bypasses flow validation, requires admin auth. Intended for human operators fixing stuck state. Logs the override for audit.
- **Add an implicit `failed -> done` reverse transition.** The flow system already has `_implicit_reverse_transitions()` — add `failed -> done` as one. Risk: agents could accidentally accept failed tasks.
- **Add a `resolved` queue as an alternative terminal state.** A human moves `failed` tasks to `resolved` (meaning "we've dealt with this") without claiming the task completed successfully. Keeps `done` semantically pure.
- **SDK method: `sdk.tasks.force_queue(task_id, queue, reason)`** — admin-only, bypasses flow validation, writes an audit log entry. This is the most general solution — works for any stuck state, not just `failed -> done`.

## Recommendation

Combine two fixes:

1. **Prevent false failures (Problem 1):** After `merge_pr` accepts a task to `done`, mark the rest of the `runs` list as non-fatal. The simplest approach: `execute_steps` takes an optional `non_fatal_after` parameter — all steps after the named step catch exceptions and log warnings. Or: check task queue in the catch-all before calling `fail_task()`.

2. **Admin recovery endpoint (Problem 2):** Add `POST /api/v1/tasks/{id}/force-queue` to the server. Requires the request to include a `reason` field. Server logs the override. SDK exposes it as `sdk.tasks.force_queue(task_id, queue, reason)`. This is the nuclear option for when state gets stuck — it should be rare if Problem 1 is fixed, but it needs to exist.

## Open Questions

- Should `force-queue` require a separate admin role, or is the standard orchestrator API key sufficient?
- Should we add a `/resolve` skill that wraps force-queue with a nice UX (shows the task, asks for confirmation, logs to issues)?
- Is the `resolved` terminal state worth adding, or is `done` sufficient for recovered tasks?

## Scope

**Server changes (separate repo):**
- Add `POST /api/v1/tasks/{id}/force-queue` endpoint
- Add audit log for forced transitions

**SDK changes:**
- Add `sdk.tasks.force_queue(task_id, queue, reason)` method

**Orchestrator changes:**
- Harden `handle_agent_result_via_flow` catch-all: check if task already in `done` before calling `fail_task()`
- Consider `non_fatal_after` pattern for post-merge steps
- Add `/resolve` skill for human recovery workflow
