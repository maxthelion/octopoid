# Path forward: systemic vs task-level failure handling

**Captured:** 2026-03-02

## Raw

> We need to change the system to respond to systemic failure (pausing and spawning a fixer agent to diagnose some fixes). Task level fixes should broadly stay the same with requires intervention. I'm not sure if the fixer agent that works on failed tasks (that we described last night) should now be redeployed to systemic issues. We should expect fewer tasks to end up in the failed queue. Anything else?

## Idea

The last few drafts (210, 216, 217) have been circling the same problem from different angles. This draft synthesises them into a path forward.

### The core insight

There are two fundamentally different kinds of failure, and they need fundamentally different responses:

**Systemic failure** — something is broken with the infrastructure, not with the task. Worktree creation fails, server is unreachable, git auth expired, agent binary missing. Every task that enters the pipeline will hit the same problem. The correct response is to **stop the line** — pause intake, diagnose the issue, fix it, resume. Continuing to process tasks through a broken pipeline creates more mess than stopping.

**Task-level failure** — something went wrong with this specific task. The LLM agent misunderstood the requirements, there's a merge conflict with this branch's changes, the task description is ambiguous. Other tasks are unaffected. The correct response is **requires-intervention** — a fixer agent investigates this specific task, decides whether to retry, adjust, or escalate to a human.

Today, both kinds of failure end up in the same place: the task gets `attempt_count` incremented, eventually hits a circuit breaker, and moves to `failed`. This conflates "the system is broken" with "this task has a problem" — hiding systemic issues behind a pile of individually-failed tasks.

### The new model

**Systemic failures → pause + system-level diagnostic agent**

When the scheduler detects a failure that isn't task-scoped (spawn failure, server error, step infrastructure failure), it:
1. Does NOT increment `attempt_count` on the task — the task is blameless
2. Puts the task back in incoming, untouched
3. Increments a system-level consecutive failure counter
4. If the counter hits 2, pauses the system automatically
5. Spawns a diagnostic agent to investigate the systemic issue

The diagnostic agent (originally designed in draft 210 for the failed queue) is redeployed here. Its job is to read the scheduler logs, identify the systemic issue, and either fix it directly (if it's something like an expired token it can refresh) or write a postmortem and escalate to a human. Once the issue is resolved, the system is unpaused and tasks resume flowing.

**Task-level failures → requires-intervention + fixer agent (unchanged)**

When an agent fails on a specific task (LLM output quality, merge conflict, test failures), the existing intervention mechanism handles it:
1. Task moves to `requires-intervention` with `needs_intervention=True`
2. Fixer agent is spawned to investigate this specific task
3. Fixer reads the task's stdout, messages, worktree, and decides: retry, adjust, or escalate
4. If the fixer can't resolve it, the task moves to `failed` — but this should be rare because most task-level failures are either retryable (bad LLM output → try again) or need human judgment (merge conflict → escalate)

### What reaches `failed` now

Very little. The `failed` queue becomes a small, high-signal list:
- Tasks where the fixer agent tried and genuinely couldn't resolve the issue
- Tasks where human judgment is required (design decisions, ambiguous requirements)
- Tasks that are no longer relevant (superseded, cancelled)

The 19 tasks currently in `failed` are mostly systemic casualties — they'd never have ended up there under this model.

### The diagnostic agent's new role

Draft 210 designed a diagnostic agent for the failed queue. With this model, its primary role shifts to systemic issues:

- **Triggered by:** system pause (consecutive non-task failures detected)
- **Reads:** scheduler logs, recent failure patterns, system state
- **Actions:** fix the issue directly (refresh token, clean up worktrees, restart services), or write a postmortem and escalate
- **Goal:** get the system back to healthy so it can be unpaused

It still has a secondary role for the (now smaller) failed queue — investigating the few tasks that genuinely couldn't be resolved by the fixer. But this should be rare enough that it might not need to be automated at all — a human reviewing 1-2 failed tasks a week is manageable.

### What needs to change

**1. Classify each failure path as systemic or task-scoped**

Go through every code path that currently calls `fail_task()` or increments `attempt_count` and classify it:

| Current path | Classification | New behaviour |
|---|---|---|
| Spawn failure (worktree, invoke, etc.) | Systemic | Don't increment, requeue, bump system failure counter |
| Server connectivity error | Systemic | Don't increment, requeue, bump system failure counter |
| Step infra failure (push_branch auth, create_pr API) | Systemic | Don't increment, requeue, bump system failure counter |
| Empty stdout / agent crash | Task-scoped* | Intervention (fixer investigates) |
| Agent reports failure | Task-scoped | Intervention (existing path) |
| Merge conflict in rebase | Task-scoped | `PermanentStepError` → intervention immediately |
| Test failures | Task-scoped | Intervention (fixer investigates) |
| Empty description | Task-scoped | Reject at creation time, not at spawn time |

*Empty stdout is ambiguous — could be a systemic issue (agent binary broken) or task-scoped (agent crashed on this specific task). If it happens on 2+ tasks in a row, it's systemic.

**2. Add a system-level failure counter and auto-pause**

The scheduler tracks consecutive non-task failures. If the counter reaches a threshold (e.g. 2), it writes a PAUSE file and stops claiming new tasks. This is the "stop the line" mechanism.

**3. Spawn diagnostic agent on system pause**

When the system auto-pauses, spawn a diagnostic agent that reads the logs, identifies the issue, and either fixes it or writes a postmortem.

**4. Fix the classification of existing error types**

- `rebase_on_base` should use `PermanentStepError` for merge conflicts (currently uses `RetryableStepError`)
- Spawn failures should not increment `attempt_count`
- Step failures should distinguish "GitHub API is down" (systemic) from "this PR can't be merged" (task-scoped)

**5. Kill orphan PIDs on lease expiry**

Lease expiry should kill the orphaned agent process, not just requeue the task.

**6. Handle `needs_continuation`**

The `needs_continuation` queue needs a consumer. An agent saying "I made progress but ran out of turns" should be re-spawned with the same worktree.

## Invariants

- **systemic-failures-pause-the-line**: When a non-task-scoped failure is detected (spawn failure, server error, step infrastructure failure), the system pauses intake rather than failing the task. Tasks are blameless and return to incoming without incrementing attempt_count.
- **auto-pause-on-consecutive-failures**: If 2+ consecutive non-task-scoped failures occur, the system auto-pauses and spawns a diagnostic agent to investigate.
- **task-failures-use-intervention**: Task-scoped failures (LLM output, merge conflicts, test failures) continue to use the requires-intervention → fixer agent path. The fixer focuses on evaluating LLM work and task-specific issues.
- **failed-queue-is-small-and-high-signal**: The failed queue contains only tasks that genuinely need human judgment — not systemic casualties. Expect single digits, not dozens.
- **fixer-can-escalate-to-systemic**: The task-level fixer agent can pause the system if it discovers the root cause is systemic rather than task-scoped. It posts a message describing the systemic problem, triggering the auto-pause and diagnostic agent flow. The classification isn't always obvious at the point of failure — the fixer is a second line of detection.
- **permanent-errors-skip-retry**: Failures that will never self-resolve (merge conflicts, permission denied) use `PermanentStepError` and go to intervention immediately, skipping the retry loop.

## Context

This draft synthesises:
- **Draft 210** — diagnostic agent for the failed queue. The agent's role shifts from "triage the failed queue" to "diagnose systemic issues when the system auto-pauses"
- **Draft 216** — codify failure modes. The key distinction (bugs vs real failures) sharpens into (systemic vs task-scoped)
- **Draft 217** — task lifecycle failure modes. The detailed code trace that revealed how each failure path works today, and where the gaps are

Related postmortems: 2026-03-01-task-868b-intervention-leak (systemic: needs_intervention leak), 2026-03-01-gatekeeper-approve-no-transition (bug: missing _perform_transition).

### Fixer agent can escalate to systemic pause

The task-level fixer agent should still log everything it finds. If, while investigating a task-level failure, it determines the root cause is actually systemic (e.g. "this task failed because the server is unreachable", or "the agent crashed because the claude binary is broken"), it should have the power to pause the line itself. It does this via the message system — posting a message describing the systemic problem — which triggers the auto-pause and diagnostic agent flow.

This is important because the systemic/task-scoped classification isn't always obvious at the point of failure. A spawn failure is clearly systemic. But "agent produced empty stdout" could be either — and it might take the fixer investigating the task to realise "this isn't a task problem, the agent binary is broken for everyone". The fixer shouldn't be limited to task-level actions if it discovers something bigger.

## Open Questions

- What's the right threshold for auto-pause? 2 consecutive failures is aggressive but safe. Should it be configurable?
- When the diagnostic agent fixes a systemic issue, should it auto-unpause? Or should a human confirm before resuming?
- Should we audit the 19 currently-failed tasks and re-classify them before building the new system? This would validate the systemic/task-scoped split.

## Possible Next Steps

1. **Audit the 19 failed tasks** — classify each as systemic or task-scoped to validate the model
2. **Add systemic failure counter** — track consecutive non-task failures at the scheduler level, auto-pause at threshold
3. **Stop incrementing attempt_count for systemic failures** — spawn failures, server errors, step infra failures don't touch the task's attempt_count
4. **Fix PermanentStepError usage** — merge conflicts in `rebase_on_base` should go to intervention immediately
5. **Kill orphan PIDs** — lease expiry kills the agent process
6. **Repurpose diagnostic agent** — shift from failed-queue triage to systemic issue diagnosis on auto-pause
7. **Add needs_continuation consumer** — re-spawn agents with "continue" instructions
