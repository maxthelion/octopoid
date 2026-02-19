---
**Processed:** 2026-02-18
**Mode:** human-guided
**Actions taken:**
- Issues 2, 3, 4 merged via TASK-334e15ee (PR #51)
- Issue 7 merged via TASK-1597e6f5 (integration test)
- Issues 1, 5 completed in PR #50 (TASK-082c8162) — not yet merged
**Outstanding items:** PR #50 needs merge (issues 1, 5). Issue 6 (warn before discard unpushed) not started.
---

# Fix Project Lifecycle: Lessons from Draft 13

**Status:** Partial
**Captured:** 2026-02-16
**Based on:** `project-management/drafts/13-2026-02-15-project-branch-lessons.md`

## Raw

> Let's create a draft to tackle the stuff brought up in draft 13. We should have a much more solid foundation to work from now.

## Context

Draft 13 documented 7 failure modes from the first multi-task project (the scheduler refactor). Several caused lost commits, orphaned worktrees, and tasks running against the wrong code. The root causes were:

1. `get_project()` reads local YAML instead of the server API
2. Worktree lifecycle doesn't handle shared branches safely
3. `approve_and_merge` doesn't push commits for no-PR tasks
4. No validation that project branch config is consistent

Since then, the refactor branch has landed with:
- Entity module split (queue_utils → sdk.py, tasks.py, projects.py, etc.)
- Hook system (hooks.py + hook_manager.py) with BEFORE_SUBMIT and BEFORE_MERGE points
- Server-side projects API (full CRUD: create, get, list, update, delete, get tasks)
- Task worktree creation with branch validation

This gives us a solid foundation to fix all 7 issues properly.

## Issues to Fix

### 1. Projects must use the server API, not local YAML

**Problem:** `get_project()`, `list_projects()`, `create_project()` all read/write local YAML files in `.octopoid/shared/projects/`. The server has a full projects API that's being ignored. Three `TODO` comments in queue_utils.py acknowledge this.

**Fix:** In `orchestrator/projects.py` (post-refactor split):
- `create_project()` → call `sdk.projects.create()` (server auto-assigns timestamps)
- `get_project()` → call `sdk._request('GET', f'/api/v1/projects/{project_id}')`
- `list_projects()` → already uses `sdk.projects.list()` — verify it works
- `activate_project()` → call `sdk._request('PATCH', f'/api/v1/projects/{project_id}', json={'status': 'active'})`
- Delete `_write_project_file()` and `get_projects_dir()` — no more local YAML

**SDK changes needed:** Add `get()`, `create()`, and `update()` methods to `ProjectsAPI` in the Python SDK. The server endpoints already exist.

### 2. Worktree cleanup: detach HEAD instead of deleting

**Problem:** When a task completes, `cleanup_task_worktree()` force-deletes the worktree. This:
- Loses the working directory for inspection/debugging
- On shared-branch projects, blocks the next task (git won't let two worktrees use the same branch)

**Fix:** Change `cleanup_task_worktree()` to:
1. Push commits (already done)
2. Detach HEAD: `git checkout --detach` in the worktree
3. Do NOT delete the worktree

This frees the branch for the next task while preserving the worktree for inspection. Add a separate housekeeping job to prune old detached worktrees after N days.

### 3. `create_task_worktree` must detach conflicting worktrees

**Problem:** If another worktree holds the target branch, `git worktree add -b <branch>` fails. Currently we force-delete the branch and recreate it.

**Fix:** Before creating a worktree, check if any existing worktree holds the branch. If so, detach it:

```python
for line in run_git(["worktree", "list", "--porcelain"], cwd=parent_repo).stdout.split("\n\n"):
    if f"branch refs/heads/{branch}" in line:
        wt_path = line.split("\n")[0].replace("worktree ", "")
        run_git(["checkout", "--detach"], cwd=wt_path, check=False)
```

### 4. Push shared branch on task completion (no-PR case)

**Problem:** `approve_and_merge()` runs BEFORE_MERGE hooks (which merge the PR). But project tasks that share a branch and don't have PRs don't push their commits. The next task creates a worktree from `origin/<branch>` and misses the unpushed work.

**Fix:** In `cleanup_task_worktree()`, the push logic already exists but only pushes `HEAD`. For shared-branch projects, it should push the shared branch explicitly:

```python
run_git(["push", "origin", f"HEAD:{branch_name}"], cwd=worktree_path)
```

This ensures the branch on origin has all commits before the worktree is detached.

### 5. Validate project branch consistency

**Problem:** Projects can be created with `branch: null` or `base_branch: "main"` when they should fork from a feature branch. This causes tasks to work on the wrong code.

**Fix:** Add validation in `create_project()`:
- `branch` is required (no null)
- If creating from a feature branch, `base_branch` must be set correctly
- Warn if `base_branch` is "main" but the current working branch is a feature branch

### 6. Warn before discarding unpushed commits

**Problem:** `create_task_worktree()` silently deletes local branches that exist but aren't on origin. If those branches have unpushed commits, the commits become dangling objects.

**Fix:** Before deleting a local branch, check for unpushed commits:

```python
local_check = run_git(["rev-parse", "--verify", branch], cwd=parent_repo, check=False)
if local_check.returncode == 0:
    # Check for commits not on origin
    unpushed = run_git(
        ["rev-list", f"origin/{branch}..{branch}", "--count"],
        cwd=parent_repo, check=False
    )
    if unpushed.returncode == 0 and int(unpushed.stdout.strip()) > 0:
        # Push before deleting, or raise an error
        run_git(["push", "origin", branch], cwd=parent_repo, check=False)
    run_git(["branch", "-D", branch], cwd=parent_repo, check=False)
```

### ~~7. Test isolation: mock at the SDK transport level~~

**Covered by TASK-7a393cef** (queue_utils refactor, rejection point 6). The refactor task already requires fixing mock paths to patch `orchestrator.sdk.get_sdk` at the canonical location.

## Implementation Plan

This should be a project with 3 sequential tasks on the refactoring branch:

### Task 1: SDK + projects.py — use server API
- Add `get()`, `create()`, `update()` to `ProjectsAPI` in the Python SDK
- Rewrite `projects.py` to use SDK instead of local YAML
- Delete `_write_project_file()`, `get_projects_dir()`
- Add project branch validation

### Task 2: Worktree lifecycle — detach instead of delete
- Change `cleanup_task_worktree()` to detach HEAD instead of deleting
- Change `create_task_worktree()` to detach conflicting worktrees
- Add push-before-discard safety check
- Add worktree pruning housekeeping job

### Task 3: Integration test — run a mini project
- Create a 2-task project programmatically
- Verify task 1 worktree is based on correct branch
- Verify task 1 completion pushes commits
- Verify task 2 worktree sees task 1's commits
- Verify worktrees are detached, not deleted

## Open Questions

- Should we keep local YAML files as a cache/fallback, or delete them entirely? The server is the source of truth, but local files provide offline visibility.
- How old should detached worktrees be before the pruner deletes them? 7 days?
