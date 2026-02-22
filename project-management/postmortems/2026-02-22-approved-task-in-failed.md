# Postmortem: Approved Tasks Stuck in Failed Queue

**Date:** 2026-02-22
**Tasks:** TASK-c0ef5fd2 (move rebase to before_merge), TASK-34138f84 (gatekeeper merge conflicts fix), TASK-00956912 (split drafts tabs)
**PRs:** #189, #188, #192
**Duration:** ~1 day of manual intervention to recover all three tasks

## Summary

Three tasks on the `feature/draft-50-actions` branch ended up in the `failed` queue despite having completed work and gatekeeper approvals. The root cause was a chicken-and-egg dependency: the gatekeeper was rejecting tasks for merge conflicts, and the task that would fix this behavior (TASK-34138f84) was itself blocked by the same issue.

## Timeline

1. **TASK-c0ef5fd2** (move rebase from before_submit to before_merge) was enqueued on `feature/draft-50-actions`
2. **Agent implemented it**, but the gatekeeper rejected it for having merge conflicts with the base branch
3. **Task was rejected 3 times** — each time the agent re-implemented it, the gatekeeper rejected it again for merge conflicts
4. **TASK-34138f84** (update gatekeeper: merge conflicts are not blocking) was created to fix this — it teaches the gatekeeper that merge conflicts should be noted but not cause rejection
5. **TASK-34138f84 was also enqueued** on the same branch, but the same gatekeeper behavior that was blocking c0ef5fd2 could block it too
6. **System was paused** for manual intervention to break the deadlock
7. **Both tasks' leases expired** during the pause — the scheduler moved them to `failed` without checking that the gatekeeper had already approved them
8. **TASK-00956912** (split drafts tabs) was also on this branch, got caught in the same pause/lease-expiry situation
9. **All three tasks required manual recovery**: rebase onto main, resolve conflicts, force-push, merge PRs, then navigate the task lifecycle manually (`failed` → `incoming` → `claim` → `submit` → `accept` → `done`)

## Root Cause

**Primary: Chicken-and-egg gatekeeper dependency.** The gatekeeper was configured to reject tasks that had merge conflicts with their base branch. TASK-c0ef5fd2 kept accumulating merge conflicts as other PRs merged to main, causing repeated rejections (3 total). The fix for this (TASK-34138f84) needed to be merged first, but it was subject to the same gatekeeper behavior — creating a circular dependency.

**Secondary: Lease expiry during system pause.** When the system was paused to manually intervene, task leases continued to tick. When they expired, the scheduler moved the tasks to `failed` without checking whether the gatekeeper had already approved them (via `result.json`). This turned completed, approved tasks into failed ones.

## Impact

- Three tasks required manual recovery through the full lifecycle
- Merge conflicts accumulated during the pause, requiring manual rebases
- ~1 day of human time spent on recovery instead of productive work
- The gatekeeper's merge-conflict rejection policy caused 3 wasted agent cycles on TASK-c0ef5fd2

## Fixes Applied

1. **TASK-34138f84 merged** — The gatekeeper now treats merge conflicts as informational rather than blocking. It notes them in its review but does not reject the task. This breaks the chicken-and-egg cycle.

2. **TASK-c0ef5fd2 merged** — Rebase moved from `before_submit` to `before_merge`, so merge conflicts at submit time are expected and not a problem.

## Outstanding Issues

**Lease expiry during system pause is still unfixed.** If the system is paused long enough for a lease to expire, approved tasks will still be moved to `failed`. Possible fixes:

1. **Don't expire leases during system pause** — skip lease expiry processing while paused
2. **Check for result.json before failing** — before expiring a lease, check if the gatekeeper already approved the task
3. **Process pending results before checking leases** — reorder scheduler processing so approvals are handled before expiry checks

## Lessons Learned

- When the gatekeeper has a policy that causes rejections, any task to fix that policy is subject to the same rejections — watch for circular dependencies
- Pausing the system doesn't pause lease timers, which can cause approved work to be lost
- Feature branches with multiple concurrent tasks are more prone to merge conflict cascades
