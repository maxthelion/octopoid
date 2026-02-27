# Fixer Agent: Task $task_id

A task has entered the `requires-intervention` queue and needs your help.

## Task

**ID:** $task_id
**Title:** $task_title
**Priority:** $task_priority
**Branch:** $task_branch

## Task Description

$task_content

## Intervention Context

The following context was recorded when this task failed:

```json
$intervention_context
```

**What this means:**
- `previous_queue`: where the task was when it failed (the transition to resume)
- `error_source`: what triggered the failure (e.g., step-failure-circuit-breaker)
- `error_message`: the actual error that occurred
- `steps_completed`: flow steps that had already succeeded before the failure
- `step_that_failed`: the specific step that raised an exception (empty if unknown)

## Your Working Directory

You are working in the task's **existing worktree** at `$worktree`. All previous work from the original agent is preserved here — commits, branch state, partial changes.

The task directory (parent of the worktree) is `$task_dir`. It contains:
- `intervention_context.json`: the context above
- `step_progress.json`: step execution progress (if available)
- `stdout.log` / `stderr.log`: logs from the failed agent run

## Available Scripts

- **`../scripts/run-tests`** — Run the project test suite

## What To Do

1. Read the intervention context carefully
2. Check `project-management/issues-log.md` for known patterns
3. Inspect the worktree: `git status`, `git log --oneline -10`, check for conflicts
4. Read any relevant error logs
5. Diagnose the root cause
6. Apply a fix
7. Record the issue in `project-management/issues-log.md`
8. Write `result.json`

## Global Instructions

$global_instructions

## Completing Your Work

When done, write a summary to stdout and exit.

**On success (you fixed the issue):** Clearly state what caused the failure and what you did to fix it. Use language like "Fixed: ..." or "Issue resolved: ...".

**If you cannot fix the issue:** Clearly explain why you couldn't fix it and what human action is needed. Use language like "Cannot fix: ..." or "Requires human intervention: ...".

**Important:** Do NOT use `outcome: "done"`. Do NOT push branches, create PRs, or call the Octopoid API. The scheduler handles everything after reading your stdout.
