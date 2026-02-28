# Self-healing task recovery: replace failed with requires-intervention + fixer agent

**Captured:** 2026-02-27

## Raw

> I think we need to think about what the failed state is intended for. Our goal should be anti-fragility and self-repairing. Most issues that come up, are relatively simple issues that we resolve in the terminal. I propose a new state/queue that is something like "requires intervention". If tasks are put into this state, then the scheduler will spawn a fixer agent to try to work out what is wrong. When they have resolved this, they remove that state, and the task can proceed as it would have before (resume its flow at the point it left off). The agent working on these fixes should be very diligent about recording issues, and diagnosing holistic fixes as well as simply sorting out the immediate issue.

## Idea

Instead of `failed` being a terminal graveyard that requires human CLI intervention, introduce a `requires-intervention` queue that triggers a **fixer agent**. The fixer diagnoses the problem, fixes it, and resumes the task's flow from where it left off. The system becomes self-healing for the class of issues we currently fix manually.

## Context

Today, when a task hits `failed`:
- Nobody is notified (except a line in a log)
- The task sits there until a human runs `/queue-status`, notices it, investigates manually, and hacks around the server's flow validation to recover it
- Most failures are mundane: git conflicts, stale state, a step that threw on a transient error, a lease that expired while the scheduler was down
- These are exactly the kinds of issues we routinely diagnose and fix in 5-10 minutes at the terminal

The `failed` queue currently has 14 tasks. Most have `outcome=done` or `outcome=success` — the work was done, but something went wrong in post-processing. A fixer agent could have resolved most of these automatically.

## Design

### New queue: `requires-intervention`

Not a replacement for `failed` — a **predecessor** to it. The flow becomes:

```
[task hits an error]
  → requires-intervention (fixer agent spawned)
    → [fixer resolves it] → resume flow from where it left off
    → [fixer can't resolve it] → failed (true terminal state, needs human)
```

`failed` still exists but means "a fixer agent tried and couldn't fix it either" — genuinely stuck. This should be rare.

### Fixer agent

A new agent role: `fixer`. The scheduler spawns it when a task enters `requires-intervention`. Like all agents, the fixer is a **pure function** — it receives a task, does work in the worktree, and writes a `result.json`. It never calls the server API, moves tasks between queues, or performs side effects. The scheduler handles all state transitions after the fixer exits.

**Input:** The fixer receives (via its rendered prompt):
- The task description and metadata
- The intervention context (error, which step failed, which steps completed, previous queue)
- Access to the task's existing worktree (git state, local changes, logs, previous result.json)

**What the fixer does (in the worktree):**
1. **Diagnose** — Read the error context, check logs, inspect git state, check for known patterns in the issues log
2. **Fix the immediate issue** — Rebase, resolve conflicts, clean up stale state, fix whatever broke
3. **Record the issue** — Write to `project-management/issues-log.md` with symptoms, root cause, and fix applied
4. **Propose systemic fixes** — If this is a recurring pattern, write a draft file to `project-management/drafts/` proposing a permanent fix
5. **Write result.json** — Report what was fixed and that the task is ready to resume

**What the fixer writes:**

```json
{
  "outcome": "fixed",
  "diagnosis": "local main had unpushed commits, git pull --rebase failed",
  "fix_applied": "ran git pull --rebase to sync local main"
}
```

Or if it can't fix the issue:

```json
{
  "outcome": "failed",
  "diagnosis": "merge conflict in src/foo.py requires human judgement"
}
```

**What the scheduler does with the result:**
- `outcome: "fixed"` → move task back to `previous_queue`, post a message summarising the fix, resume the flow from where it left off (skip completed steps)
- `outcome: "failed"` → move task to `failed` (true terminal), post a message with the diagnosis so a human knows what's wrong

### Flow integration

The process that moves a task to `requires-intervention` records an **intervention context** on the server describing where it was and how to resume:

```yaml
# Stored on the task (server-side) when entering requires-intervention
intervention_context:
  previous_queue: provisional    # where the task was
  error_source: flow-dispatch-error
  error_message: "update_changelog: git pull --rebase failed..."
  transition_in_progress: "provisional -> done"
  step_that_failed: update_changelog
  steps_completed: [merge_pr]   # steps that already ran successfully
  resume_instruction: "retry update_changelog, then complete transition to done"
```

### Communication via messages

All messages are posted by the **scheduler**, not the agent. The fixer communicates entirely through `result.json`.

**On entry to requires-intervention** (scheduler posts):

```
[scheduler] Task moved to requires-intervention.
Error: update_changelog: git pull --rebase failed (local branch has diverged)
Steps completed: merge_pr
Steps remaining: update_changelog
```

**After fixer exits** (scheduler reads `result.json` and posts):

```
[scheduler] Fixer resolved the issue.
Diagnosis: local main had unpushed commits, git pull --rebase failed
Fix applied: ran git pull --rebase to sync local main
Resuming flow from update_changelog step.
```

Or if the fixer couldn't fix it:

```
[scheduler] Fixer could not resolve the issue. Moving to failed.
Diagnosis: merge conflict in src/foo.py requires human judgement
```

This keeps the entire intervention lifecycle auditable in the task's message thread, and the fixer stays a pure function.

### What the fixer should NOT do

- **Call the server API** — it's a pure function. No `sdk.tasks.*`, no `post_message()`. It writes `result.json` and the scheduler does the rest.
- **Re-implement the task from scratch** — that's what requeue to incoming is for
- **Merge PRs or accept tasks** — it fixes the blocker, the flow does the rest
- **Ignore issues** — if it can't fix something, it writes `outcome: "stuck"` with a clear diagnosis

## Examples

### Example 1: update_changelog failure after merge

```
Task 2a06729d in provisional
→ merge_pr runs, accepts task to done
→ update_changelog throws (git pull --rebase fails)
→ Scheduler moves to requires-intervention, records intervention_context
→ Scheduler posts message: "update_changelog failed, steps_completed: [merge_pr]"
→ Fixer agent spawns in task's worktree:
  - Reads intervention context from prompt
  - Checks issues-log.md — sees this is a known pattern
  - Runs git pull --rebase to sync local main
  - Adds entry to issues-log.md
  - Writes result.json: { outcome: "fixed" }
→ Scheduler reads result, moves task back to previous queue
→ Scheduler resumes flow: skips merge_pr (completed), retries update_changelog
→ update_changelog succeeds, transition to done completes
```

### Example 2: Lease expired because scheduler was down

```
Task 543cd9d7 in claimed
→ Agent finishes, writes result.json
→ Scheduler is down (launchd plist wrong module name)
→ Lease expires, server moves to requires-intervention
→ Fixer agent spawns (once scheduler is back):
  - Reads intervention context: lease expired, original result.json exists
  - Diagnosis: scheduler was down, result never processed
  - Verifies result.json shows outcome=done, work is intact in worktree
  - Writes NEW result.json: { outcome: "fixed" }
→ Scheduler reads result, moves task back to claimed
→ Scheduler processes the original agent result normally
```

### Example 3: Rebase conflict on merge

```
Task in provisional, gatekeeper approved
→ merge_pr step: rebase fails with conflicts
→ Scheduler moves to requires-intervention
→ Fixer agent spawns:
  - Reads error: "git rebase failed: CONFLICT in src/foo.py"
  - Inspects the conflict — trivial import ordering change
  - Resolves conflict, continues rebase, commits
  - Writes result.json: { outcome: "fixed" }
→ Scheduler reads result, moves task back to provisional
→ Scheduler resumes flow: retries merge_pr (rebase now clean), succeeds
```

## Decisions

1. **Turn limit and retry cap:** 50 turns, 1 attempt. One generous shot. If the fixer can't resolve it in 50 turns, it's genuinely stuck — move to `failed` for human review.

2. **Worktree:** The fixer works in the task's existing worktree. That's where all the state is — partial commits, branches, local changes. No separate worktree.

3. **Resume mechanism:** The process that moves a task to `requires-intervention` must record an **intervention context** on the task (stored on the server) describing where it was in the flow and what the scheduler should do when it resumes. This is not just `steps_completed` — it's a full resumption instruction. The **messages system** is used for audit: the scheduler posts messages on entry and after the fixer exits (based on `result.json`). The fixer itself is a pure function — it communicates only through `result.json`, like every other agent.

4. **Visibility:** One task at a time. The fixer focuses on the single task it's assigned. But it can (and should) read the issues log (`project-management/issues-log.md`) to check if this is a known pattern and apply the documented fix. Pattern detection across multiple failures is a separate concern — the existing analyst agents or a dedicated job can handle that.

## Relationship to other drafts

- **Draft #169** (force-queue endpoint): Still useful as a human escape hatch, but the fixer agent handles most cases automatically
- **Draft #167** (refactor handle_agent_result_via_flow): The refactoring makes it easier to implement the "remember where we were in the flow" logic
- **Draft #168** (unified logging): The fixer agent needs good logs to diagnose issues — unified logging makes this much easier
- **Draft #164** (unified fail_task): `fail_task()` becomes the entry point for `requires-intervention` instead of `failed`


## Invariants

- `fixer-agent-handles-failures`: When a task enters the `requires-intervention` queue, the scheduler spawns a fixer agent. The fixer diagnoses the problem, applies a fix, and resumes the task flow from where it failed. Direct human CLI intervention should not be the first resort.
- `requires-intervention-not-terminal`: The `requires-intervention` queue is not a graveyard. Tasks in it have an associated fixer agent attempt. Only if the fixer also fails does the task need human attention.
