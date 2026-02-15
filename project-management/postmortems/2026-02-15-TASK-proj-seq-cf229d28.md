# Project branch sequencing — 3 attempts, never complete

**Task:** TASK-proj-seq-cf229d28
**Agent:** implementer-2 (x2), implementer-1 (x1)
**Attempts:** 3
**Outcome:** incomplete, ignored-feedback

## What was asked
Implement project branch sequencing: server-side branch inheritance, lazy branch creation, auto-accept, project completion detection, README docs. 7 implementation sections across 4 codebases.

## What the agent built
- Attempt 1 (implementer-2): Built ~80% correctly. Missed server-side tasks.ts change. Used non-existent functions (reviewer was wrong — they did exist). CHANGELOG not requested.
- Attempt 2 (implementer-1): Crashed. No commits. Stale result.json from attempt 1 caused scheduler to think it submitted successfully.
- Attempt 3 (implementer-2): Added client-side branch inheritance in queue_utils.py instead of server-side in tasks.ts. Created VERIFICATION.md. Didn't push commits. Connection error on submit.

## Why it went wrong
1. **Task too broad.** 7 sections, 4 codebases (server TS, scheduler PY, git_utils PY, agent scripts). Too much for one agent run.
2. **Server submodule ambiguity.** Agent may not have known how to edit submodules/server. Kept avoiding the tasks.ts change across all 3 attempts.
3. **Task file contradicted review.** Task spec code samples used `sdk.tasks.list()` and `queue_utils.get_project()`, but reviews rejected those patterns. Agent followed the task file, got rejected, then was confused.
4. **"Should fix" items ignored.** Review used "critical / should fix / minor" tiers. Agent focused on "critical" and ignored everything else.
5. **Infrastructure bug.** Stale result.json from attempt 1 was processed as attempt 2's result — a real scheduler bug (now fixed).

## Lessons
- **Break multi-codebase tasks into one task per codebase.** If it spans server + orchestrator, that's 2 tasks minimum.
- **Don't put code samples in task files that you'll reject in review.** If you want SDK calls, show SDK calls. If you want API calls, show API calls.
- **Make all review items blockers or non-blockers.** "Should fix" is ambiguous — agents treat it as optional.
- **Verify the agent can actually edit the target files.** If the task requires editing a submodule, confirm the agent has access.
