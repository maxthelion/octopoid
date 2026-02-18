---
**Processed:** 2026-02-18
**Mode:** human-guided
**Actions taken:**
- All 5 implementation items complete and merged (TASK-8f741bb, TASK-334e15ee)
- `ensure_on_branch()` added to RepoManager, `TASK_BRANCH` exported in env.sh
- Raw worktree calls wrapped in `_add_detached_worktree()` / `_remove_worktree()`
- Remaining issue (ensure_on_branch called in prepare_task_directory) tracked by TASK-6ee319d0
**Outstanding items:** none — remaining edge case tracked separately
---

# Fix Worktree Creation: Detached HEADs + Branch Lifecycle

**Status:** Complete
**Captured:** 2026-02-16

## Raw

> Agents can't start because worktree creation fails with `git worktree add -b` when the branch already exists. Need to: always create worktrees as detached HEADs, wrap worktree ops in proper methods (not raw run_git), add `ensure_on_branch()` to RepoManager, pass TASK_BRANCH env var to agents, update submit-pr script. Raw `run_git(["worktree", ...])` is an antipattern — there should be a function for doing this that is always used, rather than generic run_git.

## Context

Both implementers have 40+ consecutive failures because the scheduler tries `git worktree add -b feature/client-server-architecture` which fails — the branch already exists (it's the current working branch). This affects any project task where the project's branch is already checked out.

A partial fix was applied to `create_task_worktree()` to use `--detach` instead of `-b`, but the rest of the pipeline — agent scripts, `RepoManager`, `submit-pr` — all assume they're on a named branch. On detached HEAD, `push_branch()` gets `branch="HEAD"`, and `gh pr create --head HEAD` fails.

## Design Principles

1. **Worktrees always created as detached HEADs** — never `git worktree add -b`
2. **Wrap worktree ops in proper methods** — no raw `run_git(["worktree", ...])` scattered around
3. **`RepoManager` handles branch creation** — `ensure_on_branch()` before push/PR
4. **Scheduler tells agents their branch name** via `TASK_BRANCH` env var

## Implementation Plan

### 1. `orchestrator/repo_manager.py` — add `ensure_on_branch()`

```python
def ensure_on_branch(self, branch_name: str) -> str:
    """Create/checkout named branch if on detached HEAD."""
    status = self.get_status()
    if status.branch == branch_name:
        return branch_name
    if status.branch == "HEAD":
        result = self._run_git(["checkout", "-b", branch_name], check=False)
        if result.returncode != 0:
            self._run_git(["checkout", branch_name])  # exists locally
        return branch_name
    raise RuntimeError(f"On '{status.branch}', expected '{branch_name}' or detached HEAD")
```

Update `push_branch()` — raise clear error on detached HEAD instead of silently pushing "HEAD".

Update `create_pr(task_branch=None)` — if detached and `task_branch` provided, call `ensure_on_branch()` first.

### 2. `orchestrator/scheduler.py` — add `TASK_BRANCH` to env.sh

In `prepare_task_directory()`, compute and export `TASK_BRANCH` via `get_task_branch(task)`.

### 3. `.octopoid/agents/implementer/scripts/submit-pr` — use `TASK_BRANCH`

Read `TASK_BRANCH` from env and pass to `create_pr(task_branch=TASK_BRANCH)`.

### 4. `orchestrator/git_utils.py` — cleanup `cleanup_task_worktree()`

Handle detached HEAD gracefully — skip push if on detached HEAD (no branch = no commits to push).

### 5. `orchestrator/git_utils.py` — wrap raw worktree `run_git` calls

Extract into named functions used everywhere:

```python
def _add_detached_worktree(parent_repo, worktree_path, start_point):
    """Create a detached worktree at start_point."""
    run_git(["worktree", "add", "--detach", str(worktree_path), start_point], cwd=parent_repo)

def _remove_worktree(parent_repo, worktree_path):
    """Force-remove a worktree and prune stale refs."""
    run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=parent_repo, check=False)
    run_git(["worktree", "prune"], cwd=parent_repo, check=False)
```

Replace all raw worktree git calls in `create_task_worktree()`, `ensure_worktree()`, `cleanup_task_worktree()`, `remove_worktree()`.

## What Does NOT Change

- `get_task_branch(task)` — already correct
- `create_task_worktree()` — already uses `--detach` (just wrap the raw call)
- `ensure_worktree()` — already uses `--detach` (just wrap raw calls)
- `create_feature_branch()` — older function, not used in new flow. Deprecate later.

## Verification

1. `python3 -m orchestrator.scheduler --once --debug` — claims task, creates worktree
2. `git -C .../worktree rev-parse --abbrev-ref HEAD` → "HEAD"
3. `pytest tests/test_git_utils.py tests/test_task_worktrees.py`
4. Manual test: submit-pr creates branch and opens PR

## Open Questions

- Should `RepoManager` also own worktree lifecycle (create/cleanup), or keep those as free functions in `git_utils.py`?
- Should `create_feature_branch()` be deleted or kept for backwards compat?
