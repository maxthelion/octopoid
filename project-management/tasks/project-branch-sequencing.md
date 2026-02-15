# Implement Project Branch Sequencing

## Context

Projects group sequential tasks that build on each other's work. The schema supports this — projects have `branch` and `base_branch` fields, tasks have `project_id`, and `activate_project()` exists. But the wiring is incomplete: worktrees always base from main, tasks don't inherit the project branch, `auto_accept` is stubbed, and there's no mechanism for task 2 to see task 1's commits.

See: `project-management/drafts/11-2026-02-15-project-branch-sequencing.md` for full design.

## Implementation

### 1. Task branch inheritance (server-side)

**File:** `submodules/server/src/routes/tasks.ts` — POST handler

When creating a task with `project_id`, if `branch` is not explicitly set, inherit from the project:

```typescript
// After validation, before INSERT
if (body.project_id && !body.branch) {
  const project = await queryOne(db, 'SELECT branch FROM projects WHERE id = ?', body.project_id)
  if (project?.branch) {
    body.branch = project.branch
  }
}
```

### 2. Lazy branch creation (scheduler)

**File:** `orchestrator/scheduler.py` — in the claim flow for project tasks

When the scheduler claims the first task for a project, check if the project branch exists. If not, create it:

```python
def ensure_project_branch(task: dict) -> None:
    """Create the project branch if it doesn't exist yet."""
    project_id = task.get("project_id")
    if not project_id:
        return

    sdk = get_sdk()
    project = sdk._request("GET", f"/api/v1/projects/{project_id}")
    if not project or not project.get("branch"):
        return

    branch = project["branch"]
    base = project.get("base_branch", "main")
    parent = find_parent_project()

    # Check if branch exists remotely
    result = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=parent, capture_output=True, text=True, timeout=30
    )
    if branch in (result.stdout or ""):
        return  # Already exists

    # Create and push
    subprocess.run(["git", "fetch", "origin", base], cwd=parent, capture_output=True, timeout=60)
    subprocess.run(
        ["git", "push", "origin", f"origin/{base}:refs/heads/{branch}"],
        cwd=parent, capture_output=True, text=True, timeout=60
    )
    debug_log(f"Created project branch {branch} from {base}")
```

Call this from the implementer spawn path, after claiming but before creating the worktree.

### 3. Fetch before worktree creation

**File:** `orchestrator/git_utils.py` — `create_task_worktree()`

Before creating the worktree, fetch the latest state of the task's branch so task 2 sees task 1's pushed commits:

```python
# Before creating worktree
branch = task.get("branch", "main")
subprocess.run(
    ["git", "fetch", "origin", branch],
    cwd=parent_project, capture_output=True, timeout=60
)
```

### 4. Auto-accept for project tasks

**File:** `orchestrator/scheduler.py` — `process_auto_accept_tasks()` (currently stubbed)

Un-stub this function:

```python
def process_auto_accept_tasks() -> None:
    """Auto-accept provisional tasks where project.auto_accept is true."""
    try:
        sdk = get_sdk()
        provisional = sdk.tasks.list(queue="provisional")
        if not provisional:
            return

        for task in provisional:
            task_id = task.get("id", "")

            # Check task-level auto_accept
            if task.get("auto_accept"):
                sdk.tasks.accept(task_id=task_id, accepted_by="auto-accept")
                print(f"[{datetime.now().isoformat()}] Auto-accepted task {task_id}")
                continue

            # Check project-level auto_accept
            project_id = task.get("project_id")
            if project_id:
                project = sdk._request("GET", f"/api/v1/projects/{project_id}")
                if project and project.get("auto_accept"):
                    sdk.tasks.accept(task_id=task_id, accepted_by="project-auto-accept")
                    print(f"[{datetime.now().isoformat()}] Auto-accepted task {task_id} (project)")
    except Exception as e:
        debug_log(f"Error in process_auto_accept_tasks: {e}")
```

### 5. Direct merge to project branch (skip per-task PRs)

**File:** `orchestrator/agent_scripts/submit-pr` (or the submit logic)

When a task belongs to a project with `auto_accept`, the agent should merge its work directly to the project branch instead of creating a PR:

- Check if task has `project_id` and project has `auto_accept`
- If so: merge agent's commits to project branch, push project branch
- Write `result.json` with `outcome: "submitted"` (or "done")
- The auto-accept housekeeping job picks it up

If not easy to change the agent script directly, an alternative: let the agent create the PR as normal, but the auto-accept logic in the scheduler merges it immediately and moves the task to done. This is simpler and still achieves the same result — task 2 will see task 1's work because the PR gets merged to the project branch before task 2 starts.

### 6. Project completion

**File:** `orchestrator/scheduler.py` — new housekeeping job

```python
def check_project_completion() -> None:
    """Check if all tasks in active projects are done. Create final PR if so."""
    try:
        sdk = get_sdk()
        projects = sdk.projects.list(status="active")

        for project in projects:
            project_id = project["id"]
            branch = project.get("branch")
            base = project.get("base_branch", "main")
            if not branch:
                continue

            tasks = sdk._request("GET", f"/api/v1/projects/{project_id}/tasks")
            if not tasks:
                continue

            all_done = all(t.get("queue") == "done" for t in tasks)
            if not all_done:
                continue

            # All tasks done — create final PR
            # Use gh CLI to create PR from project branch to base
            parent = find_parent_project()
            result = subprocess.run(
                ["gh", "pr", "create",
                 "--base", base,
                 "--head", branch,
                 "--title", f"[Project] {project['title']}",
                 "--body", f"All {len(tasks)} tasks completed."],
                cwd=parent, capture_output=True, text=True, timeout=60
            )

            if result.returncode == 0:
                sdk._request("PATCH", f"/api/v1/projects/{project_id}", json={"status": "review"})
                print(f"[{datetime.now().isoformat()}] Project {project_id} complete, PR created")
    except Exception as e:
        debug_log(f"Error in check_project_completion: {e}")
```

Add to HOUSEKEEPING_JOBS (or call from run_scheduler).

### 7. Document branch behaviour in README

**File:** `README.md`

Add a section explaining:
- How projects group sequential tasks on a shared branch
- Branch is created lazily on first task claim
- Tasks inherit the project branch automatically
- Intermediate tasks auto-accept (no per-task PRs)
- Each task's worktree includes all prior tasks' commits
- Final PR created when all tasks complete
- Example workflow with CLI commands

## Acceptance Criteria

- [ ] Tasks created with `project_id` auto-inherit `project.branch`
- [ ] Project branch created lazily on first task claim (pushed to origin)
- [ ] Worktree creation fetches latest branch state first
- [ ] `process_auto_accept_tasks()` implemented and working
- [ ] Project tasks auto-accept when `project.auto_accept = true`
- [ ] Project completion creates final PR when all tasks done
- [ ] README documents project branch behaviour
- [ ] Existing tests pass
- [ ] Manual test: create project with 2 tasks, verify task 2 sees task 1's commits
