# Preserve worktrees when requeuing tasks — don't destroy completed work

**Captured:** 2026-02-27

## Raw

> Look at all the paths that can take a task back to incoming, when an agent has already worked on it. In almost all scenarios, we don't want to kill the worktree and create a new one. Write integration tests to demonstrate that this is currently happening, and create a fix.

## Idea

When a task is requeued (moved back to `incoming` after an agent has already worked on it), the scheduler currently creates a brand new worktree from current main and spawns a fresh agent that redoes all the work from scratch. The previous worktree — containing the agent's commits, branch, and all changes — is silently destroyed.

This is wasteful and wrong. In most requeue scenarios, the work is already done and just needs to be re-evaluated or pushed through the pipeline. The agent on task 68773f27 used 112 tool calls reimplementing 9 integration tests that were already committed in the previous worktree.

## Context

Three tasks (a89a0147, 0b59cdd4, 68773f27) completed successfully but were killed by the broken result handler (draft #174). When requeued to incoming, the scheduler created fresh worktrees and spawned new agents that redid all the work. The reflog proves it — the new worktree starts at the current main HEAD with no history from the first run.

## Paths that requeue a task to incoming

All of these paths can move a task back to incoming after an agent has already worked on it:

1. **Result handler returns unknown** → task goes to `failed` → human requeues to `incoming`
2. **Haiku inference error** (auth, timeout, unexpected response) → same as above
3. **Gatekeeper rejects** → task goes to `failed` → human requeues to `incoming`
4. **Rebase conflict at merge** (draft #93) → task goes to `failed` → human requeues
5. **Agent runs out of turns** → task goes to `failed` → human requeues
6. **Step failure** (e.g. merge_pr fails) → task goes to `failed` → human requeues
7. **Lease expiry** → task goes back to `incoming` automatically
8. **Manual requeue** via `/retry-failed` or direct SDK call

In scenarios 1, 2, 3, 4, and 6, the agent's work is likely correct and complete — the failure is in the pipeline, not the code. Destroying the worktree and starting over is pure waste.

In scenario 5 (ran out of turns), the work is partially done — a new agent continuing from the existing worktree is better than starting from scratch.

In scenario 7 (lease expiry), the agent might still be running — the worktree should definitely not be destroyed.

## What should happen instead

When `prepare_task_directory()` is called for a task that already has a worktree at `.octopoid/runtime/tasks/<id>/worktree`:

1. **Check if the worktree exists** and has commits beyond the base
2. **If it exists**: rebase the existing worktree onto current main (to pick up any changes that landed while the task was in failed/incoming), reset stdout/stderr/tool_counter, and reuse it
3. **If it doesn't exist** (first claim, or worktree was archived): create a fresh one as today

The agent prompt should also note that previous work may exist in the worktree, so the agent checks what's already done before starting.

## Invariants

- `worktree-preservation`: When a task is requeued after an agent has already worked on it, the existing worktree and commits are preserved. The scheduler detects an existing worktree for the task and reuses it rather than creating a fresh one from main.
- `worktree-reuse-over-recreate`: A fresh worktree is only created when no previous worktree exists for the task. Infrastructure failures (pipeline error, lease expiry, step failure), gatekeeper rejection, and manual requeue must all attempt to reuse the existing worktree.

## Open Questions

- Should the rebase be automatic, or should the scheduler just reuse the worktree as-is and let the agent handle any conflicts?
- Should the prompt tell the agent "check git log — previous work may already be done"?
- What about the case where the task file was rewritten after rejection (gatekeeper feedback)? The old commits might not match the new requirements. Should we destroy the worktree only when the task file has been modified since the last run?
- Should `prepare_task_directory()` distinguish between "infrastructure failure requeue" (keep worktree) and "task rewrite requeue" (fresh worktree)?

## Root Cause

The reuse logic **already exists** in `create_task_worktree()` (git_utils.py:272-275):

```python
if worktree_path.exists() and (worktree_path / ".git").exists():
    base_branch = task.get("branch") or get_base_branch()
    if _worktree_branch_matches(parent_repo, worktree_path, base_branch):
        return worktree_path  # ← reuse!
    # Branch mismatch — delete and recreate
    _remove_worktree(parent_repo, worktree_path)
```

But `_worktree_branch_matches` (line 198-244) checks `git merge-base --is-ancestor origin/main <worktree-HEAD>`. This fails whenever main has advanced since the worktree was created — which is always, because other agents are constantly landing work. The check asks "is origin/main an ancestor of the worktree?" but after main advances, origin/main is *ahead* of the worktree's base, so the ancestor check returns false, and the worktree is destroyed.

The fix is to loosen or invert this check. Options:
1. Check that the worktree HEAD and origin/main share a recent common ancestor (merge-base exists and is recent)
2. Always reuse if the worktree has commits beyond its base, and rebase onto current main
3. Remove the branch match check entirely — just reuse any existing worktree for the same task ID

## Possible Next Steps

- Audit `prepare_task_directory()` in scheduler.py to understand where the old worktree is destroyed
- Write integration tests that demonstrate: create task → claim → agent commits work → fail task → requeue → claim again → verify the old worktree/commits are preserved
- Fix `prepare_task_directory()` to detect and reuse existing worktrees
- Update the agent prompt template to mention checking for existing work
