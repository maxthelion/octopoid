# Postmortem: Gatekeeper approve executes steps but never transitions task to done

**Date:** 2026-03-01
**Task:** 2b09a4db ("Add integration test for run_due_jobs dispatch cycle")
**Severity:** High — completed work silently gets stuck, triggers fixer loop, ends in failed

## Symptoms

- Task 2b09a4db completed successfully: implementer wrote code, gatekeeper approved, PR #288 merged, changelog updated
- Task never reached `done` queue despite all terminal steps completing
- Fixer dispatched 3 times, each correctly diagnosing "already complete", reporting `outcome=fixed`
- Circuit breaker moved task to `failed` after 3 fixer attempts
- Fixer received empty intervention context `{}`

## Timeline

1. Implementer submits work, PR created
2. Gatekeeper approves — `_handle_approve_and_run_steps` called for `provisional -> done` transition
3. All 4 steps execute successfully: `post_review_comment`, `rebase_on_base`, `merge_pr`, `update_changelog`
4. Function returns `True` **without calling `_perform_transition()`** — task stays in `provisional`/`claimed`
5. Lease expires, task requeued to `incoming`
6. New agent claims it, fails (work already done, PR merged, branch gone)
7. `fail_task()` sets `needs_intervention=True`, fixer dispatched
8. Fixer says "fixed" but flow can't advance — wrong `previous_queue` in intervention context
9. Cycle repeats 3x until circuit breaker

## Root Cause

**File:** `octopoid/result_handler.py`, function `_handle_approve_and_run_steps` (line 613)

After successfully executing all transition steps (line 645), the function logs success and returns `True` (line 687-688) **without calling `_perform_transition(sdk, task_id, transition.to_state)`**.

Compare with the implementer path (line 432-441) which correctly calls `_perform_transition` after executing steps:

```python
# Implementer path (CORRECT) — line 440:
_perform_transition(sdk, task_id, transition.to_state)

# Gatekeeper approve path (BROKEN) — line 687:
logger.info(f"Agent {agent_name} completed task {task_id} (steps: {transition.runs})")
return True
# _perform_transition() never called
```

The steps run their side effects (merge PR, update changelog) but the task's queue is never updated on the server. The task remains claimed with an active lease that eventually expires.

## Secondary Issue

When the fixer is dispatched after lease-expiry -> requeue -> re-claim -> re-fail, the `previous_queue` in the intervention context reflects the latest failure point (`claimed` or `incoming`), not the original transition (`provisional`). So `_resume_flow` looks up the wrong transition and can't advance to `done`.

## Fix

Add the missing `_perform_transition` call at line 687 in `result_handler.py`:

```python
# After execute_steps succeeds:
_perform_transition(sdk, task_id, transition.to_state)
logger.info(f"Agent {agent_name} completed task {task_id} (steps: {transition.runs})")
return True
```

## Impact

Any task that goes through gatekeeper approval with transition steps (i.e., every `provisional -> done` transition in the default flow) will silently fail to reach `done`. The PR gets merged but the task stays stuck, eventually hitting the circuit breaker and landing in `failed`.

This likely affects every task that was approved since `_handle_approve_and_run_steps` was introduced.

## Detection

Should have been caught by an integration test that verifies the full gatekeeper-approve -> done lifecycle. Draft 187 (which ironically created this task) called for exactly this kind of test coverage.
