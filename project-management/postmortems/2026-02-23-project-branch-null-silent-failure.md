# Postmortem: Project tasks completed but never landed (PROJ-efe0fc20)

**Date:** 2026-02-23
**Severity:** Medium — work was done but had to be manually cherry-picked
**Duration:** ~2 hours (from project creation to discovering the issue)

## Summary

Created a project (PROJ-efe0fc20, "Testing Analyst Agent") with 4 sequential tasks. All 4 tasks were claimed, implemented, and approved by the gatekeeper (queue=done). But no code was pushed, no PR was created, and the project stayed in `status=draft` forever. The work existed only in isolated worktrees.

## Timeline

1. `create_project()` called without explicit `branch` parameter
2. Server stored project with `branch=null`
3. 4 tasks created with `project_id` and `blocked_by` chains, `flow=project`
4. Each task claimed, agent spawned in its own worktree on detached HEAD
5. Each agent committed code, wrote result.json with `outcome=done`
6. Gatekeeper approved each task → moved to `queue=done`
7. `check_project_completion` job ran, found all children done, but: **silently skipped because `project.branch` was null**
8. No PR created. Project stayed in `status=draft`.
9. Human discovered the issue when tasks didn't appear in dashboard

## Root Cause Chain

### 1. `create_project()` doesn't auto-generate a branch name

`orchestrator/projects.py:13-43` — accepts `branch=None` and passes it directly to the SDK. No default like `feature/PROJ-{id}` is generated. Compare with `create_task()` which falls back to `get_base_branch()`.

### 2. `create_task()` silently falls back to `main` when project has no branch

`orchestrator/tasks.py:472-482` — when `project_id` is set but no `branch`, it fetches the project to get its branch. Since `project.branch` is null, the condition `if project and project.get("branch")` fails. Falls back to `get_base_branch()` → "main". No warning logged.

### 3. Each agent works in isolation

`orchestrator/git_utils.py:163-195` — `get_task_branch()` tries the same project branch lookup, fails the same way, falls back to standalone task branch. Each worktree is created from `origin/main` independently. The agents cannot see each other's work.

### 4. `check_project_completion` silently skips branchless projects

`orchestrator/scheduler.py:~1715-1718`:
```python
if not project.get("branch"):
    debug_log(f"check_project_completion: project {project_id} has no branch, skipping")
    continue
```

This is a safety gate (without a branch, `create_project_pr` would raise RuntimeError). But it's completely silent — no warning to the user, no inbox message, no state change. The project just stays in `draft` forever.

## Why It Was Silent

Every layer has a correct defensive check, but none of them report the problem to the user:

| Layer | What happens | Reports to user? |
|-------|-------------|-----------------|
| `create_project(branch=None)` | Accepts it | No |
| `create_task()` branch lookup | Falls back to main | No |
| `get_task_branch()` | Falls back to task branch | No |
| Agent worktree | Created on detached HEAD | No (normal behaviour) |
| `check_project_completion` | Skips silently | No (debug log only) |

The system worked exactly as designed at each layer, but the aggregate result was a complete pipeline break with zero user-visible feedback.

## Fixes Required

### Immediate: Auto-generate project branch

In `create_project()`, if no `branch` is provided, generate one:
```python
if not branch:
    short_id = project_id.replace("PROJ-", "")[:8]
    branch = f"feature/{short_id}"
```

This is the root cause — everything downstream would work correctly if the project had a branch.

### Defensive: Warn on branchless project completion

In `check_project_completion()`, instead of silently skipping, post an inbox message:
```python
if not project.get("branch"):
    sdk.messages.create(
        task_id=f"project-{project_id}",
        from_actor="scheduler",
        to_actor="human",
        type="warning",
        content=f"Project {project_id} has all tasks done but no branch set. Cannot create PR."
    )
    continue
```

### Defensive: Warn when task falls back from project branch

In `create_task()`, when project branch lookup fails, log a warning:
```python
if project_id and not project.get("branch"):
    print(f"WARNING: Project {project_id} has no branch. Task will use base branch instead.")
```

## Lessons

1. **Silent fallbacks compound.** Each layer's fallback was individually reasonable, but 4 silent fallbacks in sequence created a complete pipeline break with no trace.
2. **Required fields should be required.** If a project needs a branch to function, `create_project()` should either require it or generate it — not accept null and let downstream code deal with it.
3. **Safety gates should notify.** A debug log that nobody reads is not a notification. If `check_project_completion` is going to skip a project, it should tell the user.

## Related

- Dashboard also didn't show project tasks because `flow=project` had no registered flow definition on the server — fixed separately by pooling unregistered flows into the default tab (commit `b1b0982`).
