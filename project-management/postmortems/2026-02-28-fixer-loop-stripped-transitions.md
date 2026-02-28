# Fixer Loop: Tasks Cycling Between Fixer and Intervention Indefinitely

**Date:** 2026-02-28
**Tasks affected:** dc1d2d53, bcee551d, 072394ea, 6693d4d5 (and likely others)
**Duration:** ~2 hours of wasted fixer agent cycles before manual intervention
**Root cause:** Stripped flow transitions + fixer "fixed" outcome triggering `_resume_flow` with empty steps, which then calls `_perform_transition` to submit — but the task's queue state doesn't match what the server expects.

## Timeline

1. Scheduler syncs flows with stripped transitions (`{from, to}` only — no `runs`)
2. Implementer agent completes work (makes commits in worktree)
3. Result handler runs `handle_agent_result_via_flow` for `claimed → provisional`
4. `transition.runs` is empty (stripped), so `execute_steps` is skipped entirely
5. Steps like `push_branch` and `create_pr` never run — no PR is created, no PR number stored
6. `_perform_transition` calls `sdk.tasks.submit()` → task moves to `provisional`
7. Gatekeeper transition (`provisional → done`) also has empty runs → `merge_pr` skipped
8. Task reaches `done` without a PR being created or merged — ghost completion
9. **For tasks where the agent crashed before stdout was written:** empty stdout triggers intervention
10. Fixer claims the task, sees work is complete, reports "fixed"
11. `handle_fixer_result` clears `needs_intervention`, calls `_resume_flow`
12. `_resume_flow` loads the flow (still stripped), gets empty `remaining_steps`, calls `_perform_transition`
13. `_perform_transition` tries `sdk.tasks.submit()` — **409 Conflict** (task is in `claimed`, not in valid state for submit, or already submitted)
14. Exception caught at line 1072 → `fail_task()` called with `source="fixer-resume-error"`
15. `fail_task` sees `needs_intervention=False` (cleared at step 11) → treats as first failure → calls `request_intervention()` → sets `needs_intervention=True`
16. **Loop re-enters at step 10** — another fixer is spawned

## The Loop Mechanics

```
Fixer reports "fixed"
  → handle_fixer_result clears needs_intervention (line 1062)
  → _resume_flow fails with 409 (line 1068-1071)
  → fail_task sees needs_intervention=False (line 1079)
  → fail_task treats as FIRST failure → request_intervention (line 477)
  → needs_intervention=True again
  → scheduler spawns another fixer
  → repeat
```

The critical bug: **clearing `needs_intervention` before `_resume_flow` succeeds** creates a state where a resume failure is treated as a brand-new first failure, not a fixer failure. The flag acts as a "has the fixer already tried?" counter, but it's decremented before the fixer's work is verified.

## Why the Fixer Can't Fix This

The fixer is designed to fix code/implementation issues. But this failure has nothing to do with the code — it's a flow infrastructure bug. The fixer correctly identifies "the work is done" every time, but:

1. It can't fix the stripped transitions (server-side state)
2. It can't force the task through an invalid queue transition
3. Its "fixed" report triggers the exact same failing code path every time

The fixer has no way to signal "the work is done but the infrastructure is broken — stop trying." Its only options are "fixed" (triggers resume → fails → loops) or "failed" (moves to terminal failed, but then `fail_task` routes back to intervention because `needs_intervention` was cleared).

## Issues to Fix

### 1. Don't clear `needs_intervention` until resume succeeds (loop prevention)

In `handle_fixer_result` (line 1060-1064), `needs_intervention` is cleared **before** `_resume_flow` is called. If resume fails, `fail_task` sees `needs_intervention=False` and treats it as a first failure — restarting the loop.

**Fix:** Move the `needs_intervention=False` update to after `_resume_flow` succeeds, or use a different mechanism to track fixer attempts.

### 2. Add a circuit breaker on fixer attempts

There's no limit on how many times the fixer can cycle on a task. A simple counter (e.g. `attempt_count` or a message count check) would prevent infinite loops.

**Fix:** Before spawning a fixer, count `intervention_reply` messages on the task. If > N (e.g. 3), move directly to `failed` without spawning another fixer.

### 3. Fixer needs an "infrastructure-broken" outcome

The fixer can only report "fixed" or "failed". When it detects the work is done but can't progress the task, it has no good option. A third outcome like "stuck" or "needs-human" that moves to `failed` without going through the intervention cycle would prevent the loop.

## Relationship to Other Postmortems

This is a downstream consequence of the stripped-transitions bug documented in `2026-02-28-ghost-completion-no-pr-number.md`. That postmortem covers the root cause (scheduler flow sync). This postmortem covers the amplification effect — how a single bug in flow sync caused unbounded fixer loops that wasted agent resources.

## Resolution

- Root cause (stripped transitions) fixed in `ff2612e`
- Stuck tasks manually requeued via API or deleted
- Loop prevention (issues 1-3 above) not yet implemented
