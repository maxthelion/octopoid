# Postmortem: Task Stuck in Claimed After Agent Completion + Duplicate Claims

**Status:** Idea
**Captured:** 2026-02-19

## Incident 1: TASK-e37bc845 stuck in `claimed` despite work being done

### What happened

TASK-e37bc845 (Fix create_pr step) was claimed by implementer-1. The agent completed the work:
- Result JSON shows `{"outcome": "done"}`
- stdout.log contains a full summary of the fix
- PR #87 was created and is CLEAN/MERGEABLE
- The worktree has the commit

But the task remained in `claimed` queue instead of transitioning to `provisional`. The `submitted_at` field was set to `2026-02-18 21:49:00` on the server, but the queue was still `claimed`.

### Impact

The task sat in claimed for ~11 hours. No human would know it was done without manually inspecting the runtime directory. The dashboard showed it as "in progress" with no-pid (agent not running).

### Manual recovery

Had to manually call `sdk.tasks.submit()` to move it to provisional, then `approve_and_merge()` to complete it.

### Possible causes

- The agent's finish script may have failed silently after the agent exited
- The submit API call may have partially succeeded (set `submitted_at`) but failed to transition the queue
- A version conflict / optimistic locking failure on the server side (task has version=18, indicating many updates)
- The `rejection_count: 2` suggests this task has been through the cycle multiple times — one of the re-claims may have reset state incorrectly

### Investigation needed

- Check if the finish script (`scripts/finish.sh`) logs errors anywhere
- Look at server logs around 2026-02-18 21:49:00 for the submit endpoint
- Check if the server's state machine allows `claimed → provisional` when `submitted_at` is already set (possible idempotency issue on re-claim)
- Review the flow dispatch logic for edge cases with high-version tasks

## Incident 2: implementer-1 has two tasks claimed simultaneously

### What happened

Queue status shows implementer-1 claiming both:
- TASK-e37bc845 (P0, the stuck task above)
- TASK-12056c21 (P1, "Add explicit rebase instructions to gatekeeper")

Agents should only claim one task at a time. Having two claims means either:
1. The scheduler's `max_claimed` check didn't prevent the second claim
2. The first task's claim wasn't properly released before the second was claimed
3. The stuck state from Incident 1 caused the scheduler to think implementer-1 was idle (no running PID), so it claimed another task

### Likely root cause

Given Incident 1: the agent finished and exited (PID gone), but the task stayed in `claimed`. The scheduler saw implementer-1 as idle (no running process) and assigned it a new task, not realizing the first claim was still active on the server.

### Fix needed

The scheduler's claim logic should check: "does this agent already have a task in `claimed` queue?" before claiming a new one. Currently it may only check PID liveness, not server-side claim state.

## Open Questions

- Is there a `claimed_by` filter on the task list API? Could the scheduler query `sdk.tasks.list(queue='claimed', claimed_by='implementer-1')` before claiming?
- Should the finish script be made more resilient — retry the submit call if it fails?
- Should there be a reconciliation job that detects claimed tasks with no running agent and either requeues or submits them?

## Possible Next Steps

- Add a pre-claim check in the scheduler: query server for existing claims by this agent
- Add retry logic to the finish script's submit call
- Add a "stale claim detector" to the lease expiry housekeeping job (TASK-96a53880 in incoming)
- Investigate server logs for the failed transition
