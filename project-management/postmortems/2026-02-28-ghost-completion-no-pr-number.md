# Ghost Completion: Task in Done Without PR Merge

**Date:** 2026-02-28
**Tasks:** cdd7cdce, 6693d4d5
**Impact:** Fix never landed on main despite task showing as done; duplicate task got stuck in fixer loop

## Summary

Task `cdd7cdce` ("Fix analyst scripts: replace orchestrator/ references with octopoid/") reached `done` queue with `pr_number: None`. The gatekeeper approved it, but the work never actually merged to main because there was no PR to merge. The codebase-analyst scripts remained broken on main.

A duplicate task `6693d4d5` was created for the same fix. The implementing agent completed the work and created PR #270, but crashed before writing stdout. This triggered intervention, and the fixer entered a loop: it kept seeing the work was done and reporting "fixed", but the task couldn't progress because it was stuck between `failed` and the 409 conflicts on submit/accept.

## Root Cause: Missing PR Number

Task `cdd7cdce` has `pr_number: None` and `pr_url: None`. This means one of:
1. The `create_pr` step was skipped or failed silently
2. The PR was created but the number was never stored back on the task
3. The `merge_pr` step ran with `pr_number=None` and the verify step caught it (log shows: `merge_pr verify failed: PR #None not in MERGED state after merge attempt`) but the task was already moved to `done`

The log entry confirms the third scenario: the merge step failed verification, attempted to reject, got a 409 (task already in done), and the catch-all handler correctly didn't move it back to failed. But the damage was done — the task sat in `done` with unmerged work.

## Root Cause: Fixer Loop on 6693d4d5

1. Original agent completed work, crashed without writing stdout
2. Scheduler flagged for intervention (empty stdout)
3. Fixer saw work was complete, reported "fixed"
4. Flow tried to submit → 409 (wrong queue state)
5. Task bounced back to intervention → another fixer → same result
6. After ~20 cycles, eventually landed in `failed` with no valid transitions out

The default flow has no `failed → done` or `failed → incoming` transition, so the task was permanently stuck.

## Resolution

- PR #270 (from `6693d4d5`) was manually reviewed and merged via `gh pr merge`
- Task `6693d4d5` was deleted via API since no flow transition could move it to done
- Task `cdd7cdce` remains in `done` as a ghost completion (work never merged independently)

## Issues to Address

1. **No `failed → incoming` transition in default flow.** Tasks that end up in `failed` can never be requeued through the API. Need a `failed → incoming` transition (possibly admin-only) so stuck tasks can be recovered without deletion.

2. **`merge_pr` running with `pr_number=None`.** The `merge_pr` step should have a pre-check that fails fast if `pr_number` is None, before the task reaches `done`. Currently the verify catches it too late — after the task has already transitioned.

3. **Ghost completions are invisible.** There's no way to detect that a task in `done` has unmerged work. Consider adding a post-done verification step or a dashboard indicator for tasks in `done` with `pr_number=None`.

4. **Fixer loop has no circuit breaker.** The fixer kept cycling on the same task ~20 times with the same result. Need a max-attempts counter that moves the task to `failed` permanently after N fixer attempts and flags it for human attention.

## Symptoms to Add to Issues Log

| Symptom | Likely cause | See |
|---|---|---|
| Task in `done` with `pr_number: None` | `merge_pr` step failed but task already transitioned to done | This postmortem |
| Fixer cycling on same task 10+ times | Underlying issue is not fixable by the fixer (e.g. wrong queue state); needs circuit breaker | This postmortem |
| Task stuck in `failed` with no way to requeue | Default flow has no `failed →` transitions; must delete and recreate | This postmortem |
