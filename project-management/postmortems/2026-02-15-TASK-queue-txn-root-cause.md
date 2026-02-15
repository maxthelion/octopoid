# ROOT CAUSE: Agents branch from main but target code only exists on feature branch

**Tasks:** TASK-queue-txn-a60c69cd (3 attempts), TASK-projseq-2-84aafa63 (1 attempt)
**Outcome:** wrong-target (all attempts)

## Discovery

After 3 failed attempts on TASK-queue-txn and 1 on TASK-projseq-2, we investigated what the agents actually see. Both task files contained exact line numbers, before/after code, and explicit "DO NOT" lists. The prompt.md confirmed the agents received the full instructions. So why did they keep modifying the wrong code?

**The target functions don't exist in the agent's worktree.**

## Root cause

Agent worktrees branch from `main`. But the functions we asked them to modify only exist on `feature/client-server-architecture`:

- `_handle_submit_outcome()`, `_handle_fail_outcome()`, `_handle_continuation_outcome()` — added in commit `24309bb` (state-first refactor), only on `feature/client-server-architecture`
- `check_project_completion()` in scheduler.py — added in the project-seq PR, only on `feature/client-server-architecture`

Neither has been merged to `main`.

### What the agent sees

1. Opens `orchestrator/scheduler.py` in their worktree (based on `main`)
2. Searches for `_handle_submit_outcome` — **not found**
3. Searches for "queue transition" or "sdk.tasks.update" — finds `db.update_task_queue()` calls
4. Concludes that must be what the task is about
5. Wraps those calls in try/except — wrong code path, but the only one available

### What we thought the agent saw

1. Opens `orchestrator/scheduler.py` with our latest code
2. Finds the three `_handle_*_outcome()` functions
3. Wraps the `sdk.tasks.update()` calls

## Why we didn't catch this

- We wrote task descriptions referencing code on our working branch
- We assumed the agent would see the same code we see
- The task `branch` field was set to `main` (default), not `feature/client-server-architecture`
- The worktree creation code uses the task's `branch` field to determine the base

## Fix

Set the task's `branch` field to the correct base branch when creating tasks:

```python
sdk.tasks.create(
    id=task_id,
    branch='feature/client-server-architecture',  # NOT 'main'
    ...
)
```

Or merge `feature/client-server-architecture` to `main` before creating tasks that depend on its code.

## Additional contributing factor: Global instructions conflict

The global agent instructions (appended to every prompt) say:

> **After Every Change:**
> 1. **CHANGELOG.md** — Add an entry under `## [Unreleased]`
> 2. **README.md** — Update if your change affects user-facing behaviour

This directly contradicts task-level "Do NOT add CHANGELOG entries" instructions. The agent must reconcile conflicting instructions, with the global ones appearing authoritative.

## Lessons

1. **Always set the correct `branch` on tasks.** If the code to modify only exists on a feature branch, the task MUST target that branch.
2. **Verify the target code exists on the task's branch** before creating the task. If it doesn't, the agent literally cannot do the work.
3. **When an agent repeatedly makes the "same mistake", investigate what they actually see** — the problem may be environmental, not instructional.
4. **Global instructions should not contradict task-level instructions.** Either remove the blanket CHANGELOG/README rules from global instructions, or add an exception mechanism (e.g. "unless the task says otherwise").
5. **Don't blame the agent for 3 attempts when the code doesn't exist in their worktree.** We wasted 3 agent runs and hours of review time on an impossible task.
