# Scheduler Worktree Branch Mismatch Detection

**Status:** Partial
**Captured:** 2026-02-17

## Raw

> If a task's branch is different from an existing worktree, delete the worktree and create a new one from the correct branch.

## Idea

When the scheduler picks up a task that already has a worktree directory (e.g. from a previous failed attempt or requeue), the worktree may be based on the wrong branch. Currently the scheduler reuses the existing worktree without checking. If the task was requeued with a corrected branch, or the worktree was left behind from a different task, the agent works off stale code.

The scheduler should detect branch mismatches before spawning an agent:

1. Check if a worktree already exists for the task
2. If it does, compare the worktree's base commit against `origin/<task.branch>`
3. If they diverge (or the worktree is on a different branch entirely), delete the worktree and create a fresh one from the task's branch

## Implementation Status

The basic mismatch detection was implemented in `orchestrator/git_utils.py`:
- `_worktree_branch_matches()` (line 198) checks if `origin/<branch>` is an ancestor of the worktree's HEAD
- `create_task_worktree()` (line 272) calls this and recreates the worktree on mismatch

## Remaining Problem: Overly Aggressive Mismatch on Feature Branches

The ancestor check causes **false positives on feature branches**. Observed on task `39a33501` (targeting `feature/draft-50-actions`):

1. Implementer creates worktree from `origin/feature/draft-50-actions`, does work, pushes branch `agent/39a33501`
2. Other PRs (#179, #180, #181) merge into `feature/draft-50-actions`, moving the tip forward
3. Gatekeeper spawns to review — `create_task_worktree()` checks if `origin/feature/draft-50-actions` (new tip) is an ancestor of the worktree HEAD (old commit)
4. It's NOT an ancestor (the worktree is behind), so `_worktree_branch_matches()` returns False
5. Worktree gets nuked and recreated from the new tip — destroying the agent's work
6. Gatekeeper sees a clean worktree with no agent changes, rejects
7. Loop repeats on next attempt

The check direction is wrong for this case. It verifies `origin/branch` is ancestor of HEAD, but after the base branch moves forward, the worktree is simply *behind* — not on the wrong branch. This is normal for any feature branch workflow where the base keeps receiving PRs.

### Possible fixes

1. **Reverse the check**: Instead of "is origin/branch an ancestor of HEAD?", check "do HEAD and origin/branch share a common ancestor that is on the branch?" (i.e. are they diverged from the same branch, not from completely different branches). `git merge-base --is-ancestor HEAD origin/<branch>` would check if HEAD is reachable from origin/branch, which is more correct.

2. **Check the merge-base**: Use `git merge-base HEAD origin/<branch>` and verify it's recent enough / on the right branch. If the merge-base is a commit from `origin/main` rather than `origin/feature/...`, the worktree is on the wrong branch. If it's a commit from the feature branch, the worktree is just behind.

3. **Skip the check for reviewers**: The gatekeeper should be looking at the agent's pushed branch, not recreating the worktree from the base. The worktree for review should be created from the agent's branch, not the task's base branch.

4. **Track the worktree's original base**: Store which branch the worktree was created from (e.g. in a `.octopoid-base-branch` file in the worktree). On subsequent checks, compare the stored branch name to the task's branch. If they match, the worktree is valid even if the base has moved forward.

## Context

This came up when TASK-ed00d313 (agent pool model) was created with `branch: main` due to a DB default bug. The agent worked off `main` instead of `feature/client-server-architecture`. After rejecting and requeuing with the correct branch, the old worktree would still be sitting there based on `main`. The scheduler needs to detect this and start fresh.

The more recent issue (2026-02-21) with task `39a33501` shows the opposite problem: the worktree IS on the right branch but the branch moved forward, so the ancestor check fails. The worktree gets destroyed and recreated, losing the agent's work.

Related: Draft #22 (worktree detached HEAD lifecycle), Draft #5 (worktree sweeper).

## Open Questions

- Should this also apply to `ensure_worktree()` for long-lived agent worktrees, or only `create_task_worktree()`?
- What about worktrees with uncommitted work? Should we warn/fail rather than silently deleting?
- Option 4 (stored base branch file) is simplest but adds a file. Is that acceptable?

## Possible Next Steps

- Fix `_worktree_branch_matches()` to handle the "worktree is behind the base branch" case
- Option 4 (store base branch name in worktree) is probably the safest fix
- Add a test case for the "base branch moved forward" scenario
