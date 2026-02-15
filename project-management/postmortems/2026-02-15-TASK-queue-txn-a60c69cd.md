# Queue transition logging — 2 attempts, wrong code path both times

**Task:** TASK-queue-txn-a60c69cd
**Agent:** implementer-2 (x1), implementer-1 (x1)
**Attempts:** 2
**Outcome:** wrong-target

## What was asked
Wrap `sdk.tasks.update()` calls in `_handle_submit_outcome()`, `_handle_fail_outcome()`, and `_handle_continuation_outcome()` with try/except so queue transition failures are logged instead of silently ignored.

## What the agent built
Both attempts wrapped `db.update_task_queue()` and `db.accept_completion()` calls in queue_utils.py, migrate.py, and scheduler.py. These are the OLD database-based code path behind `is_db_enabled()` guards — not used in v2.0. The actual bug (unwrapped `sdk.tasks.update()` in the three `_handle_*_outcome()` functions) was never touched.

## Why it went wrong
1. **Task file wasn't specific enough.** Original task file mentioned `handle_agent_result()` and `_handle_*_outcome()` helpers but didn't give exact line numbers or show the current code that needed wrapping.
2. **Agent found similar-looking code and "fixed" it.** Searching for "queue transition" led the agent to `update_task_queue()` calls in queue_utils.py — these look relevant but are the wrong code path.
3. **v1 vs v2 confusion.** The codebase still contains the old DB-based code alongside the new SDK-based code. The agent couldn't distinguish which was active.
4. **Rejection feedback wasn't detailed enough first time.** First rejection said "fixes the wrong code path" but didn't give exact line numbers or show the current/desired code.

## Lessons
- **Show the exact current code and the exact desired code.** Don't just say "wrap X in try/except" — show the before and after with line numbers.
- **Include explicit "DO NOT CHANGE" lists.** When the codebase has dead code that looks similar to the target, explicitly list files/functions to avoid.
- **When old and new code paths coexist, be very explicit about which is active.** The agent will find the first matching pattern, which may be the wrong one.
- **Name the exact functions, not just the file.** "Fix scheduler.py" is too vague. "_handle_fail_outcome() at line 993" is unambiguous.
