# Postmortem: Manual Rebase of PR #103 (TASK-rich-detail)

**Status:** Idea
**Captured:** 2026-02-19

## What Happened

TASK-rich-detail (rich task detail view) went to the gatekeeper for review. The gatekeeper correctly found 5 test failures and merge conflicts on PR #103, and rejected the task back to incoming with rebase instructions.

Instead of letting the system handle the retry, the human operator (me, Claude) manually rebased PR #103 in the worktree. This was wrong — the system already had a functioning path for this.

## Why Manual Intervention Was Wrong

The flow already handles this correctly:

```
provisional → gatekeeper rejects → incoming → implementer re-claims → agent rebases + fixes
```

Specifically:
1. `reject_with_feedback()` in `orchestrator/steps.py` appends explicit rebase instructions to the rejection reason
2. The rejection posts a PR comment with the feedback (visible to both humans and agents)
3. The task returns to `incoming` queue
4. An implementer agent claims it and receives the rejection feedback, which includes:
   ```
   Before Retrying:
   Rebase your branch onto the base branch before making changes:
   git fetch origin
   git rebase origin/feature/client-server-architecture
   ```
5. The agent rebases, fixes the test failures, and resubmits

By manually rebasing, I:
- Bypassed the system's built-in retry mechanism
- Fixed only the rebase but not the test failures (the agent would have done both)
- Set a precedent for human intervention that undermines trust in the system

## Correct Actions

1. **Do nothing.** The gatekeeper rejection flows the task back to incoming automatically.
2. **Verify the rejection completed.** Check `/queue-status` to confirm the task moved to incoming.
3. **If stuck in claimed (orphan bug):** Use `sdk.tasks.reject()` to manually move it to incoming — but don't touch the code/branch.
4. **Wait for an agent to re-claim and fix both issues** (rebase + test failures).

## What Works Today

- `guard_pr_mergeable` (scheduler.py:216-278): Detects CONFLICTING PRs on tasks in provisional/claimed and rejects them back to incoming with rebase instructions. This prevents the gatekeeper from entering an infinite loop re-approving a task that can't be merged.
- `reject_with_feedback()` (steps.py:59-98): Appends rebase instructions (using the correct `get_base_branch()`) to rejection feedback. The agent sees this when it re-claims the task.
- Default flow (`on_fail: incoming`): Gatekeeper rejections send tasks back to incoming for implementer retry.

## What's Missing (Improvement Opportunities)

### 1. No automated rebase agent

The architecture doc (docs/architecture.md:305-317) describes a `rebaser` role that:
- Finds tasks with `needs_rebase=TRUE`
- Checks out the branch in a review worktree
- Rebases onto the base branch
- Runs tests
- Pushes with `--force-with-lease`

**This role was never implemented.** The file `orchestrator/roles/rebaser.py` doesn't exist. Currently, rebasing is delegated to the implementer agent via rejection feedback, which works but is less efficient (uses Claude turns for a mechanical git operation).

### 2. `needs_rebase` field is unused

The `needs_rebase` column exists in the server schema (migrations/0001_initial.sql) and is fully supported in the SDK and TypeScript types. But:
- Nothing in the Python orchestrator sets `needs_rebase=TRUE`
- Nothing queries for tasks needing rebase
- `guard_pr_mergeable` rejects tasks but doesn't set the flag
- The `check_stale_branches()` function described in architecture.md doesn't exist

### 3. `rebase_on_main` hook is hardcoded to `main`

In scheduler.py:905-908, the `rebase_on_main` hook generates instructions that say `git rebase origin/main` regardless of the actual base branch. This is stale from v1. However, `reject_with_feedback()` correctly uses `get_base_branch()`, so the rejection path works properly.

## Recommended Fixes

### Short-term (pragmatic)
- **No action needed.** The existing rejection + re-claim path works. Agents get rebase instructions and fix both the rebase and the underlying issues. The main fix is operational discipline: don't manually intervene.

### Medium-term
1. **Fix `rebase_on_main` hook** to use `get_base_branch()` instead of hardcoded `main`
2. **Set `needs_rebase` in `guard_pr_mergeable`** when rejecting for conflicts, to enable future automation
3. **Add a dashboard indicator** for tasks that need rebase (similar to the ORPH badge)

### Long-term
4. **Implement the rebaser role** — a lightweight (no Claude) agent that rebases branches automatically, saving Claude turns for actual code work

## Lesson

Trust the system. When a task fails review, the flow handles it. Manual intervention should be reserved for cases where the system is genuinely broken (e.g., orphan bug preventing queue transitions), not for speeding up a retry cycle that the system can handle on its own.
