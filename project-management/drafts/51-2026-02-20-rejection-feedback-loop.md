# Inject Gatekeeper Rejection Feedback Into Implementer Retry Prompts

**Status:** Idea
**Captured:** 2026-02-20

## Raw

> The `$review_section` in the implementer prompt template is always hardcoded to `""`. When a gatekeeper rejects a task, the detailed feedback is posted as a PR comment and passed to `sdk.tasks.reject()`, but the server doesn't store the reason anywhere. On retry, the implementer gets the exact same prompt with no mention of the rejection — it has to rediscover the problems from scratch. In practice, feedback only reaches the agent when a human manually rewrites the task file.

## Idea

Close the rejection feedback loop so implementer agents automatically see gatekeeper feedback on retry, without human intervention.

The gatekeeper already writes structured, actionable rejection comments (file paths, line numbers, what's wrong, how to fix it). This information exists but is siloed in PR comments that the implementer never reads.

## Current Flow (Broken)

```
1. Implementer writes code, writes result.json
2. Scheduler runs steps: push_branch, create_pr, submit_to_server
3. Task moves to provisional
4. Gatekeeper reviews, writes result.json with decision=reject + detailed comment
5. Scheduler calls reject_with_feedback():
   - Posts comment to PR via gh api
   - Calls sdk.tasks.reject(reason=comment)
   - Server increments rejection_count, clears claim fields
   - Server DOES NOT store the rejection reason
6. Task moves back to incoming
7. Implementer claims again
8. prepare_task_directory() renders prompt with review_section=""
9. Agent starts with NO knowledge of why it was rejected
```

## Proposed Fix

### Option A: Store rejection reason on server, inject into prompt

1. **Server**: Add `last_rejection_reason TEXT` column to tasks table. The `/reject` endpoint already receives `reason` — store it.
2. **Orchestrator**: In `claim_task()`, the claimed task dict now includes `last_rejection_reason`.
3. **Scheduler**: In `prepare_task_directory()`, if `task.get("rejection_count", 0) > 0`, populate `$review_section` with the stored rejection reason:
   ```
   ## Previous Review Feedback (Attempt {rejection_count})

   Your previous implementation was rejected by the gatekeeper. Address the following issues:

   {last_rejection_reason}
   ```
4. **Prompt template**: `$review_section` already exists in the right place — just needs to be populated.

### Option B: Read PR comments at spawn time

1. In `prepare_task_directory()`, if `rejection_count > 0` and `pr_number` exists, read the latest review comment from the PR via `gh api repos/{owner}/{repo}/pulls/{pr_number}/comments`.
2. Inject the comment text into `$review_section`.
3. No server changes needed.
4. Downside: requires `gh` CLI and network access at spawn time, adds latency, and fails if rate-limited (which we already are).

### Option C: Write rejection to task runtime directory

1. In `reject_with_feedback()`, also write the rejection comment to `{task_dir}/last_rejection.md`.
2. In `prepare_task_directory()`, if `last_rejection.md` exists, read it into `$review_section` before cleaning stale files.
3. No server changes needed, no network call needed.
4. Downside: relies on task_dir persisting between cycles (it does currently, but could be cleaned up).

## Recommendation

**Option A** is the cleanest — the rejection reason is already sent to the server, it just isn't stored. One migration, one line in claim_task, one block in prepare_task_directory. The data lives where it should (on the server) and survives task directory cleanup.

**Option C** is the quickest to implement if we don't want to touch the server.

## Context

Discovered while investigating why TASK-review-card was stuck after 2 rejections. The gatekeeper gave detailed, specific feedback (wrong timestamp field, wrong code path, missing StatusBadge), but the implementer kept making the same mistakes because it never saw the feedback. The task file also had a path mismatch (separate bug), so the agent was working from the title alone — but even with the correct task file, it wouldn't have seen the rejection feedback.

Of the 7 tasks in the done queue with `rejection_count > 0`, all required manual human intervention to incorporate the gatekeeper's feedback into the task file before the retry succeeded.

## Open Questions

- Should `$continuation_section` also be populated for `needs_continuation` retries? (Same empty-string pattern.)
- Should the rejection history be cumulative (show all rejections) or just the latest?
- Should we cap `rejection_count` and auto-fail after N rejections? (Currently `max_rejections: 3` exists in v1 config but isn't enforced in v2.)

## Possible Next Steps

- Implement Option A (server migration + orchestrator change) as a task
- Or implement Option C as a quick win with no server dependency
- Add a `$continuation_section` fix at the same time
