# Fix REFACTOR project branch setup

## Problem

REFACTOR-01 and REFACTOR-02 both ran from `origin/main` instead of `origin/feature/client-server-architecture` because:

1. Project `PROJ-scheduler-agent-refactor` has `branch: null` and `base_branch: "main"`
2. With no project branch, `get_task_branch()` falls through to per-task `agent/REFACTOR-xx` branches
3. Per-task branches are isolated — REFACTOR-02 can't see REFACTOR-01's commits
4. Both were based on main, not the feature branch where all other work lives

## Fix

### 1. Update project in DB

Set a shared project branch and correct the base:

```
UPDATE projects
SET branch = 'refactor/scheduler-agent',
    base_branch = 'feature/client-server-architecture'
WHERE id = 'PROJ-scheduler-agent-refactor';
```

### 2. Clean up worktrees and local branches

```bash
# Remove worktrees
git worktree remove --force .octopoid/runtime/tasks/REFACTOR-01-de692452/worktree
git worktree remove --force .octopoid/runtime/tasks/REFACTOR-02-ca43136d/worktree
git worktree prune

# Delete local branches (commits are on wrong base, not worth keeping)
git branch -D agent/REFACTOR-01-de692452
git branch -D agent/REFACTOR-02-ca43136d
```

### 3. Requeue both tasks

```
-- REFACTOR-01: back to incoming, clear claimed state
UPDATE tasks
SET queue = 'incoming',
    claimed_by = NULL,
    claimed_at = NULL,
    submitted_at = NULL,
    completed_at = NULL,
    commits_count = 0,
    version = version + 1
WHERE id = 'REFACTOR-01-de692452';

-- REFACTOR-02: back to incoming, set blocked_by to REFACTOR-01
UPDATE tasks
SET queue = 'incoming',
    claimed_by = NULL,
    claimed_at = NULL,
    blocked_by = 'REFACTOR-01-de692452',
    version = version + 1
WHERE id = 'REFACTOR-02-ca43136d';
```

### 4. Verify

After requeue, `get_task_branch()` for any REFACTOR task will:
- See `project_id = PROJ-scheduler-agent-refactor`
- Look up project, find `branch = "refactor/scheduler-agent"`
- Return `"refactor/scheduler-agent"` as the branch name

Then `create_task_worktree()` will:
- Use `task.get("branch")` = `"feature/client-server-architecture"` as `base_branch`
- Set `start_point = "origin/feature/client-server-architecture"`
- Create worktree with `git worktree add -b refactor/scheduler-agent <path> origin/feature/client-server-architecture`

REFACTOR-01 runs, commits to `refactor/scheduler-agent`, pushes.
REFACTOR-02 unblocks, gets the same branch with REFACTOR-01's commits already there.

## What about REFACTOR-03 through REFACTOR-12?

They're all in incoming with `branch: feature/client-server-architecture`. Once the project branch is set, `get_task_branch()` will route them all to `refactor/scheduler-agent`. They should also have `blocked_by` set to chain them sequentially — otherwise they'll race and clobber each other on the shared branch.
