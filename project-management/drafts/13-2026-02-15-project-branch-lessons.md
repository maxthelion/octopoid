# Lessons Learned: Project Branch Setup

From the REFACTOR project (PROJ-scheduler-agent-refactor) — 2026-02-15.

## What went wrong

### 1. Missing `get_main_branch` import in git_utils.py

`create_task_worktree()` called `get_main_branch()` but never imported it. This was a latent NameError that never fired because:
- `queue_utils` defaults every task's `branch` to `"main"`, so the fallback path was never reached
- Every test patched `create_task_worktree` entirely — the function body never ran

**Fix:** Added the import. Added 6 unit tests that actually exercise the function body.

**Rule:** If a function has fallback paths, test them. Patching the entire function in callers' tests is not coverage.

### 2. Project created on server but `get_project()` reads local YAML

The project was created via the API, so it existed on the server. But `get_project()` in `queue_utils.py` reads from `.octopoid/shared/projects/PROJ-*.yaml` files — there's a `TODO` comment acknowledging this. No local YAML existed, so `get_project()` returned `None`, and `get_task_branch()` fell through to per-task `agent/<id>` branches instead of the shared project branch.

**Fix:** Created the local YAML file manually.

**Rule:** When creating a project, ensure the local YAML file is created too. Better yet, fix `get_project()` to read from the API (the TODO).

### 3. Project had `branch: null` and `base_branch: "main"`

The project was created without a branch, and with `base_branch: "main"` instead of `feature/client-server-architecture`. So even if `get_project()` had worked, there was no shared branch, and tasks would have been based on main.

**Fix:** Set `branch` and `base_branch` on the project.

**Rule:** When creating a project, always set:
- `branch`: the shared branch all tasks will work on
- `base_branch`: the branch it should fork from

### 4. Completed task worktree blocked the next task

After REFACTOR-01 finished and was approved, its worktree still existed, holding a lock on the `agent/REFACTOR-01-de692452` branch. REFACTOR-02 couldn't create a worktree on the same branch because git doesn't allow two worktrees on one branch.

**Fix:** Manually removed REFACTOR-01's worktree.

**Rule:** The `approve_and_merge` flow (or task completion) should clean up the worktree. If tasks share a project branch, the previous task's worktree MUST be removed before the next task starts. This might need to be enforced in the scheduler.

### 5. Tasks based on wrong branch (main instead of feature)

REFACTOR-01 and REFACTOR-02 both created worktrees from `origin/main` instead of `origin/feature/client-server-architecture`. The task had `branch: "feature/client-server-architecture"` but this was used to determine the base, and the bug in #1 meant the fallback logic was fragile.

**Fix:** The import fix (#1) plus project branch setup (#3) resolved this.

### 6. REFACTOR-01 commits lost — never pushed to origin

During the manual fix-up we:
1. Deleted the local branch with `git branch -D agent/REFACTOR-01-de692452` (to clean up before requeue)
2. Requeued REFACTOR-01, which recreated the branch from `origin/feature/client-server-architecture`
3. REFACTOR-01 ran again, made new commits at `d686108`
4. Approved REFACTOR-01, removed its worktree
5. But the commits were **never pushed to origin**

When REFACTOR-02 started, `create_task_worktree` found the local branch at `feff33b` (the recreated version, not the one with REFACTOR-01's work), deleted it, and created a fresh worktree from `origin/feature/client-server-architecture`. REFACTOR-01's commits (`a320d5b`, `d686108`) became dangling objects — still recoverable via `git show d686108` but not on any branch.

**Root cause:** The `approve_and_merge` flow for project tasks (no PR) doesn't push the shared branch to origin. So the commits only existed locally, and the branch recreation wiped them.

**Rule:** Before approving a project task, ensure its commits are pushed to origin. The project branch should be pushed after every task completes so the next task can pick it up via `origin/<branch>`. `create_task_worktree` always fetches and works from `origin/`, so unpushed local commits are invisible to it.

**Recovery:** Commits were recovered from dangling objects (`git branch -f agent/REFACTOR-01-de692452 d686108`), pushed to origin, REFACTOR-02's worktree removed, and REFACTOR-02 requeued. On third attempt it should now see REFACTOR-01's commits via `origin/agent/REFACTOR-01-de692452`.

**Recurring pattern:** This "failed with 0 commits" issue repeated on every subsequent refactor task (REFACTOR-03 through REFACTOR-06). Each time the agent completed its work and committed locally, but the server reported 0 commits and marked the task as failed. Manual intervention was needed each time: push the branch, detach the worktree, move to provisional, approve. REFACTOR-06 (`4eab01a` — test: add comprehensive unit tests for scheduler refactor) was the first to be handled by an automated monitor script.

### 7. Raw curl caused silent failures during fix-up

Several of the manual interventions above used raw `curl` commands to hit the API. These were fragile — null values in JSON caused parse errors, timeouts produced empty responses that were silently swallowed, and there was no validation of what actually changed. The Python SDK (`get_sdk()`) and `queue_utils` helpers exist for a reason: they handle retries, validate responses, and raise on errors.

**Rule:** Use the SDK or `queue_utils` functions for API operations, not raw curl. If the SDK is missing a method (e.g. `projects.update()`), add it rather than working around it.

## Checklist for creating new projects

1. **Set project branch** — pick a name (can be the first task's branch, e.g. `agent/TASK-xxx`)
2. **Set base_branch** — the branch to fork from (usually the feature branch, not main)
3. **Create local YAML** — until `get_project()` reads from the API, a file must exist at `.octopoid/shared/projects/PROJ-*.yaml`
4. **Chain tasks with blocked_by** — sequential tasks on a shared branch will clobber each other if they run in parallel
5. **Verify branch is free** — completed task worktrees should have HEAD detached so the shared branch is available for the next task

## Key design improvement: detach HEAD instead of deleting worktrees

Currently, when a project task completes, its worktree holds a lock on the shared branch — git won't let two worktrees check out the same branch. We've been force-removing worktrees to free the branch, which loses the working directory for inspection.

Better approach: when a task finishes (or when `create_task_worktree` needs the branch), run `git checkout --detach` in the completed task's worktree. This:
- **Frees the branch** immediately for the next task
- **Preserves the worktree** for inspection, debugging, or review
- **Enables parallel project work** — multiple tasks could work from the same project branch if each detaches before the next checks it out
- **Is cheap and reversible** — the worktree stays at the same commit, just no longer holding the branch ref

Implementation: in `create_task_worktree`, before `git worktree add -b <branch>`, check if another worktree has that branch checked out. If so, detach it:
```python
# Find any worktree holding our branch and detach it
for wt in run_git(["worktree", "list", "--porcelain"], cwd=parent_repo).stdout.split("\n\n"):
    if f"branch refs/heads/{branch}" in wt:
        wt_path = wt.split("\n")[0].replace("worktree ", "")
        run_git(["checkout", "--detach"], cwd=wt_path, check=False)
```

## Code changes needed

- [ ] Fix `get_project()` to read from server API, not local YAML (the existing TODO)
- [ ] Project creation flow should auto-create the local YAML as a stopgap
- [ ] Detach HEAD in completed task worktrees instead of removing them (see above)
- [ ] `create_task_worktree` should detach any existing worktree holding the target branch
- [ ] Add validation: when creating a project with tasks on `feature/*`, `base_branch` should match
- [ ] `approve_and_merge` for project tasks (no PR) must push the shared branch to origin before marking done
- [ ] `create_task_worktree` should warn/error if it's about to discard unpushed commits on a branch it's recreating
