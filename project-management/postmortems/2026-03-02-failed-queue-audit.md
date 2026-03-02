# Audit of 19 failed tasks: systemic vs task-scoped classification

**Date:** 2026-03-02

## Summary

Of 19 tasks in the failed queue, **17 are systemic casualties** of the `_perform_transition` bug (postmortem 2026-03-01-gatekeeper-approve-no-transition). Only 1 is a genuine task-level failure, and 1 was the intervention leak bug.

This validates the model from draft 218: nearly all failed tasks are there because of systemic bugs, not task-level problems.

## The dominant pattern: gatekeeper approve → stuck → fixer loop → circuit breaker

14 of 19 tasks show the same pattern:
1. Implementer completes work → outcome=done
2. Steps run successfully (push, PR created, tests pass)
3. Gatekeeper approves
4. **Bug:** `_handle_approve_and_run_steps` runs terminal steps but never calls `_perform_transition()` → task stuck in provisional
5. Fixer spawned → reads stdout → says "fixed" (because it is — work is done)
6. Flow resume fails (same missing transition bug)
7. Repeat 3 times → fixer circuit breaker → failed

This is the `_perform_transition` bug. Every one of these tasks had their work completed and their PRs approved. The system just couldn't move them to done.

## Classification

### Systemic: `_perform_transition` bug (14 tasks)

All gatekeeper-approved, fixer kept saying "fixed" but the circuit breaker fired because the flow resume hit the same bug every time.

| Task | PR | PR Status | Work done? |
|------|-----|----------|-----------|
| 2b09a4db | #288 | MERGED | Yes |
| d4dcc7b5 | #277 | MERGED | Yes |
| 61cc36d6 | #274 | OPEN | Yes (not merged) |
| bcee551d | #272 | MERGED | Yes |
| 326df326 | #278 | OPEN | Yes (not merged) |
| cdd7cdce | #279 | OPEN | Yes (not merged) |
| 072394ea | #273 | MERGED | Yes |
| 676ad0ae | #276 | MERGED | Yes |
| 5007934d | #283 | OPEN | Yes (not merged) |
| ccc41941 | — | — | Yes (no PR created) |
| 992c2841 | #285 | OPEN | Yes (not merged) |
| 2c544e93 | #286 | MERGED | Yes |
| 1ce7fa93 | #287 | OPEN | Yes (not merged) |
| a80d12ff | — | — | Yes (no PR) |

**Action:** Tasks with merged PRs (6) → force-through to done. Tasks with open PRs (6) → review whether work is still relevant, merge or close. Tasks with no PR (2) → check worktree for commits.

### Systemic: intervention leak bug (1 task)

| Task | PR | Issue |
|------|-----|-------|
| 868b0322 | — | `needs_intervention` leaked through lease expiry, causing dual agent claim (postmortem 2026-03-01-task-868b-intervention-leak) |

**Action:** Already re-enqueued to incoming. Bug fixed.

### Systemic: fixer/agent crashes with empty stdout (3 tasks)

| Task | PR | Issue |
|------|-----|-------|
| 3f777310 | — | Implementer done + GK approved, but fixer crashed with empty stdout during flow resume |
| c5ee4700 | — | Same pattern — GK approved, fixer couldn't resume |
| 16c7a502 | — | Same pattern — GK approved, fixer empty stdout |

These are also `_perform_transition` casualties with an added layer: the fixer itself crashed, producing empty stdout. Likely resource exhaustion or the intervention leak causing concurrent agents.

**Action:** Check worktrees for commits. If work was done, force-through or re-create PRs.

### Task-scoped: genuine task-level failure (1 task)

| Task | PR | Issue |
|------|-----|-------|
| 1ea7b68d | — | "Add async checks to flow system" — GK rejected once, then approved, but the work itself had issues. Multiple rejections suggest the task description was ambiguous or the feature design wasn't clear. |

**Action:** Review whether this feature is still wanted. If so, rewrite the task with clearer requirements.

## Conclusion

**18 of 19 failed tasks (95%) are systemic casualties.** They are all victims of the `_perform_transition` bug, not task-level problems. Under the systemic pause model from draft 218, none of these would have reached failed:

- The first task to hit the bug would have been detected as systemic (fixer saying "fixed" but flow resume failing = infrastructure problem, not task problem)
- The system would have paused after 2 consecutive failures
- A diagnostic agent would have identified the missing `_perform_transition` call
- 0 tasks failed instead of 19

The `_perform_transition` bug is now fixed (PR #289, merged 2026-03-01). The 6 tasks with merged PRs should be force-moved to done. The remaining tasks need individual review of their open PRs.
