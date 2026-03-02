# Fixer Agent: Task $task_id

A task has `needs_intervention=true` set and needs your help.

## Task

**ID:** $task_id
**Title:** $task_title
**Priority:** $task_priority
**Branch:** $task_branch

## Task Description

$task_content

## Intervention Context

The following context was recorded when this task failed (from the intervention request message):

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
- `intervention_context.json`: fallback context (may be empty if loaded from messages)
- `step_progress.json`: step execution progress (if available)
- `stdout.log` / `stderr.log`: logs from the failed agent run

## Available Scripts

- **`../scripts/run-tests`** — Run the project test suite
- **`../scripts/pause-system "<reason>"`** — Trigger a systemic pause (PAUSE file + system_health.json). Use when the failure is infrastructure-wide, not specific to this task.

## Systemic vs Task-Scoped Failures

Before diving into a fix, determine whether this failure is **task-scoped** (something wrong with this specific task) or **systemic** (something wrong with the infrastructure that will affect every task).

**Escalate to systemic pause when ANY of these are true:**
- The same error appears in multiple recent task failures — check other tasks' logs in `.octopoid/runtime/tasks/*/stdout.log` to look for a pattern
- The failure is clearly infrastructure-related: server unreachable, API authentication expired, Claude binary missing or broken
- Your own tools are broken: cannot read files, cannot run commands, git operations fail with auth errors
- Every task hitting this flow step fails, not just this one — look at step_that_failed and check if other tasks share it

**Keep it task-scoped (do NOT escalate) when:**
- The failure is a merge conflict specific to this branch
- Tests fail because of this task's code changes
- The task description is ambiguous or contradictory
- The LLM agent produced low-quality output (wrong code, incomplete implementation, misunderstood requirements)

### How to escalate to systemic pause

If you determine the failure is systemic:

1. Call the pause script with a clear reason:
   ```bash
   ../scripts/pause-system "Claude binary not found — all agents will fail"
   ```
   This writes the PAUSE file and updates `system_health.json`, halting the scheduler on its next tick.

2. End your stdout with:
   ```
   SYSTEMIC_ESCALATION: <brief one-line description of the systemic issue>

   <Detailed explanation: what you found, what evidence points to it being systemic,
   what needs to be investigated or fixed before the system can resume.>
   ```

The scheduler reads your stdout, detects `SYSTEMIC_ESCALATION:`, posts your explanation as a message on this task, and requeues the task blameless (no attempt_count penalty). A diagnostic agent will be spawned to investigate.

## What To Do

1. Read the intervention context carefully
2. Check `project-management/issues-log.md` for known patterns
3. Inspect the worktree: `git status`, `git log --oneline -10`, check for conflicts
4. Read any relevant error logs
5. Diagnose the root cause
6. Apply a fix
7. Record the issue in `project-management/issues-log.md`
8. Write your outcome to stdout

## Global Instructions

$global_instructions

## Completing Your Work

When done, write a summary to stdout and exit.

**On success (you fixed the issue):** Clearly state what caused the failure and what you did to fix it. Use language like "Fixed: ..." or "Issue resolved: ...".

**If you cannot fix the issue:** Clearly explain why you couldn't fix it and what human action is needed. Use language like "Cannot fix: ..." or "Requires human intervention: ...".

**Important:** Do NOT use `outcome: "done"`. Do NOT push branches, create PRs, or call the Octopoid API. The scheduler handles everything after reading your stdout.
