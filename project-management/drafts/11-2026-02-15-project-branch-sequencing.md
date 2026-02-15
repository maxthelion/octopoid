# Project Branch Sequencing

## Problem

Projects group sequential tasks that build on each other's work. Task 2 needs task 1's commits. Task 3 needs 1+2. At the end, one PR merges all accumulated work to the base branch.

The schema supports this — projects have `branch`, `base_branch`, tasks have `project_id`, and `activate_project()` creates the git branch. But the wiring is incomplete: worktrees always base from main, tasks don't inherit the project branch, `auto_accept` is stubbed, and there's no mechanism for task 2 to see task 1's commits.

## What Already Works

| Feature | Location | Status |
|---------|----------|--------|
| Project `branch` + `base_branch` fields | `packages/shared/src/project.ts` | Schema exists |
| Task `project_id` field | `packages/shared/src/task.ts` | Schema exists |
| `activate_project()` creates git branch | `orchestrator/queue_utils.py:1892` | Code exists |
| Merge to project branch (not main) | `orchestrator/roles/orchestrator_impl.py:378` | Works for orchestrator_impl |
| `auto_accept` field on projects + tasks | Schema + `process_auto_accept_tasks()` | Stubbed (returns immediately) |
| `SKIP_PR` parsed from task files | `orchestrator/queue_utils.py:370` | Parsed but never used |
| CLI: `octopoid project create --branch` | `packages/client/src/commands/project.ts` | Works |

## What's Missing

### 1. Project branch created lazily on first task claim

Currently: `activate_project()` exists but must be called manually.

Needed: When the scheduler claims the first task for a project, it should:
1. Check if the project has a `branch` set
2. If the branch doesn't exist yet, create it from `base_branch`
3. Push it to origin so worktrees can use it

This should happen in the scheduler's claim flow, not ahead of time. The project branch is an implementation detail, not something the user creates manually.

### 2. Task inherits project branch

Currently: Tasks are created with whatever `branch` the caller passes (defaults to "main").

Needed: When a task is created with a `project_id`:
- Server-side: if `project.branch` is set and `task.branch` is not explicitly provided, set `task.branch = project.branch`
- This ensures all project tasks target the same branch without the caller having to know it

### 3. Worktrees base from project branch

Currently: `create_task_worktree(task)` creates a worktree from `origin/{task.branch}`. If the project branch has accumulated commits from prior tasks, this works IF the branch has been pushed.

This actually should work already if task.branch is set correctly (fix #2). But we need to ensure:
- `git fetch origin` runs before worktree creation (to pick up latest project branch state)
- The worktree is created from `origin/{project.branch}`, which includes all prior task commits

### 4. Intermediate tasks skip PR, commit directly to project branch

Currently: Every task creates a per-task feature branch (`agent/{task-id}-{timestamp}`), pushes it, and creates a PR.

For project tasks, intermediate tasks should:
- Work on a feature branch in their worktree (for safety)
- When submitting: merge to the project branch directly instead of creating a PR
- Push the project branch
- The task moves to done (auto-accepted) without gatekeeper review

Only the final task (or project completion) creates a PR from the project branch to the base branch.

Options for detecting "intermediate vs final":
- `skip_pr` flag on individual tasks (explicit)
- All project tasks skip PR except the last in the dependency chain
- Project-level setting: `auto_accept: true` means all tasks auto-accept

### 5. Auto-accept for project tasks

Currently: `process_auto_accept_tasks()` in scheduler.py is stubbed.

Implement it:
```python
def process_auto_accept_tasks():
    sdk = get_sdk()
    provisional = sdk.tasks.list(queue="provisional")
    for task in provisional:
        if task.get("auto_accept"):
            sdk.tasks.accept(task["id"], accepted_by="auto-accept")
            continue
        # Check parent project
        project_id = task.get("project_id")
        if project_id:
            project = sdk._request("GET", f"/api/v1/projects/{project_id}")
            if project and project.get("auto_accept"):
                sdk.tasks.accept(task["id"], accepted_by="project-auto-accept")
```

### 6. Project completion

When the last task in a project completes:
1. Create a PR from `project.branch` to `project.base_branch`
2. Update project status to "review" or "complete"
3. The PR shows the full diff of all accumulated work

Detection: Check if all tasks with `project_id` are in done/failed queue. If all done, trigger completion. Could be a housekeeping job in the scheduler.

## Proposed Flow

```
1. User creates project:
   octopoid project create "Scheduler Refactor" --branch feature/scheduler-refactor --base feature/client-server-architecture --auto-accept

2. User creates tasks with project_id:
   → Server auto-sets task.branch = project.branch

3. Task 1 claimed:
   → Scheduler sees project.branch doesn't exist as git branch yet
   → Creates branch from base_branch, pushes to origin
   → Creates worktree from origin/feature/scheduler-refactor
   → Agent works, commits to worktree
   → Submit: merges to project branch (not PR), pushes
   → Auto-accepted (project.auto_accept = true)
   → blocked_by cleared on Task 2

4. Task 2 claimed:
   → Creates worktree from origin/feature/scheduler-refactor
   → Worktree now contains Task 1's commits
   → Agent works, commits
   → Submit: merges to project branch, pushes
   → Auto-accepted

5. ... repeat for all tasks ...

6. Last task completes:
   → Scheduler detects all project tasks done
   → Creates PR: feature/scheduler-refactor → feature/client-server-architecture
   → Project status → "review"
```

## Implementation Order

1. **Task branch inheritance** (server-side, ~10 lines) — When creating a task with project_id, inherit project.branch
2. **Lazy branch creation** (scheduler, ~20 lines) — Create project branch on first task claim
3. **Auto-accept** (scheduler, ~15 lines) — Implement `process_auto_accept_tasks()`
4. **Direct merge to project branch** (agent scripts/submit-pr, ~30 lines) — If task.project_id and project.auto_accept, merge to project branch instead of creating PR
5. **Project completion** (scheduler housekeeping, ~30 lines) — Detect all tasks done, create final PR
6. **Fetch before worktree** (git_utils, ~5 lines) — Ensure `git fetch origin` before creating worktree

Total: ~110 lines of new code, mostly in scheduler and agent scripts. The schema is already correct.

## What This Means for the Current Refactor Project

We created `PROJ-scheduler-agent-refactor` with 12 sequential tasks. To use this properly:

1. Update the project record: set `branch` to generate lazily, `base_branch` to `feature/client-server-architecture`, `auto_accept` to true
2. Update task branch fields to match
3. Implement at minimum: branch inheritance (#1), fetch-before-worktree (#6), and auto-accept (#3)
4. The agents can then run through the chain, each seeing the prior task's work

Alternatively: implement project-branch sequencing as a prerequisite task (task 0) before the 12 refactor tasks. It's small enough (~110 lines) and would be the first real test of the project system.
