# Scheduler Worktree Branch Mismatch Detection

**Status:** Idea
**Captured:** 2026-02-17

## Raw

> If a task's branch is different from an existing worktree, delete the worktree and create a new one from the correct branch.

## Idea

When the scheduler picks up a task that already has a worktree directory (e.g. from a previous failed attempt or requeue), the worktree may be based on the wrong branch. Currently the scheduler reuses the existing worktree without checking. If the task was requeued with a corrected branch, or the worktree was left behind from a different task, the agent works off stale code.

The scheduler should detect branch mismatches before spawning an agent:

1. Check if a worktree already exists for the task
2. If it does, compare the worktree's base commit against `origin/<task.branch>`
3. If they diverge (or the worktree is on a different branch entirely), delete the worktree and create a fresh one from the task's branch

## Context

This came up when TASK-ed00d313 (agent pool model) was created with `branch: main` due to a DB default bug. The agent worked off `main` instead of `feature/client-server-architecture`. After rejecting and requeuing with the correct branch, the old worktree would still be sitting there based on `main`. The scheduler needs to detect this and start fresh.

Related: Draft #22 (worktree detached HEAD lifecycle), Draft #5 (worktree sweeper).

## Open Questions

- Should we check the exact commit ancestry, or just compare the branch name? Branch name comparison is simpler but less precise.
- Should this also apply to `ensure_worktree()` for long-lived agent worktrees, or only `create_task_worktree()`?
- What about worktrees with uncommitted work? Should we warn/fail rather than silently deleting?

## Possible Next Steps

- Add branch check to `create_task_worktree()` — if worktree exists, verify it's based on the right branch
- If mismatch, call `_remove_worktree()` then recreate
- Log the mismatch clearly so it's visible in scheduler debug output
