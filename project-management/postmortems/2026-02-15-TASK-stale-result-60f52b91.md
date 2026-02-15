# Stale result.json cleanup — 1 attempt, right idea wrong location

**Task:** TASK-stale-result-60f52b91
**Agent:** implementer-1
**Attempts:** 1
**Outcome:** incomplete (correct fix, wrong wiring, failed to submit)

## What was asked
Add cleanup of stale `result.json` and `notes.md` in `prepare_task_directory()` before setting up a new agent run.

## What the agent built
Created a NEW `prepare_task_directory(task_id)` function (different signature from existing `prepare_task_directory(task, agent_name, agent_config)`) and called it from `create_task_worktree()` in git_utils.py — introducing a circular import concern. The cleanup logic itself was correct. Also failed to call submit-pr, so no result.json was written (ironic given the task).

## Why it went wrong
1. **Task file said "in prepare_task_directory()" but didn't emphasize it already exists.** Agent created a new function with the same name but different signature instead of adding to the existing one.
2. **Agent put cleanup in git_utils instead of scheduler.** The task said "should go early in prepare_task_directory()" but the agent interpreted this as "create a new function and call it from worktree creation."
3. **Agent didn't submit.** Wrote a summary to stdout but never called the submit-pr or finish script. Scheduler found no result.json and moved to failed.

## Lessons
- **When modifying an existing function, say "add to the existing function at line X" not just "in prepare_task_directory()".** The agent may create a new function rather than modifying the existing one.
- **Always specify the exact file path when there could be ambiguity.** "orchestrator/scheduler.py line 740" is unambiguous.
