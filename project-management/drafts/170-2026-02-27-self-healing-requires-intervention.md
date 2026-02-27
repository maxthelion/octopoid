# Self-healing task recovery: replace failed with requires-intervention + fixer agent

**Status:** Idea
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

A new agent role: `fixer`. The scheduler spawns it when a task enters `requires-intervention`.

**Input:** The fixer receives:
- The task (full metadata, current queue position, flow, which transition was being processed)
- The error/reason that caused the intervention (from `fail_task()` or equivalent)
- The task's runtime directory (worktree, result.json, logs, stderr)
- The task's message thread (PR comments, rejection feedback, etc.)

**Responsibilities:**
1. **Diagnose** — Read the error, check logs, inspect the worktree, check git state, check PR status
2. **Fix the immediate issue** — Rebase, retry a step, clean up stale state, fix a merge conflict, etc.
3. **Resume the flow** — Move the task back to whatever queue it was in before the error, so normal flow processing continues
4. **Record the issue** — Write to the issues log (`project-management/issues-log.md`) with symptoms, root cause, and fix applied
5. **Propose systemic fixes** — If the issue is a recurring pattern (e.g. "update_changelog keeps failing because of ff-only"), create a draft via the SDK proposing a permanent fix

### Flow integration

The `requires-intervention` state needs to remember where the task was in its flow so it can resume:

```yaml
# Stored on the task when entering requires-intervention
intervention_context:
  previous_queue: provisional    # where the task was
  error_source: flow-dispatch-error
  error_message: "update_changelog: git pull --ff-only failed..."
  transition_in_progress: "provisional -> done"
  step_that_failed: update_changelog
  steps_completed: [merge_pr]   # steps that already ran successfully
```

When the fixer resolves the issue, it moves the task back to `previous_queue` and the flow resumes. If steps already completed (like `merge_pr`), the flow should skip them and continue from where it left off.

### What the fixer should NOT do

- Re-implement the task from scratch (that's what requeue to incoming is for)
- Merge PRs or accept tasks (it fixes the blocker, the flow does the rest)
- Ignore issues — if it can't fix something, it should explain why and move to `failed`

## Examples

### Example 1: update_changelog failure after merge

```
Task 2a06729d in provisional
→ merge_pr runs, accepts task to done
→ update_changelog throws (git pull --ff-only fails)
→ Catch-all moves to requires-intervention (not failed)
→ Fixer agent spawns:
  - Reads error: "git pull --ff-only failed"
  - Diagnosis: local main has unpushed commits from another changelog update
  - Fix: runs git pull --rebase, retries update_changelog
  - Records issue in issues-log.md
  - Notices this is the 3rd time this has happened → creates draft proposing --rebase fix
  - Moves task back to done (merge_pr already accepted it)
```

### Example 2: Lease expired because scheduler was down

```
Task 543cd9d7 in claimed
→ Agent finishes, writes result.json
→ Scheduler is down (launchd plist wrong module name)
→ Lease expires, server moves to requires-intervention
→ Fixer agent spawns (once scheduler is back):
  - Reads context: lease expired, result.json exists with outcome=done
  - Diagnosis: scheduler was down, result never processed
  - Fix: re-claims task, submits result, processes normally
  - Records issue: "scheduler was down for 5h due to module rename in plist"
```

### Example 3: Rebase conflict on merge

```
Task in provisional, gatekeeper approved
→ merge_pr step: rebase fails with conflicts
→ Task moves to requires-intervention
→ Fixer agent spawns:
  - Reads error: "git rebase failed: CONFLICT in src/foo.py"
  - Inspects the conflict — it's a trivial import ordering change
  - Resolves conflict, continues rebase, pushes
  - Moves task back to provisional, flow retries merge_pr
```

## Open Questions

- Should the fixer agent have a turn limit? What happens if it uses all its turns without fixing the issue?
- Should there be a retry limit (e.g. max 2 fixer attempts before moving to truly-failed)?
- Does the fixer need its own worktree, or does it work in the task's existing worktree?
- How does "resume flow from where it left off" work mechanically? Do we need a `steps_completed` field on the task, or does each step need to be idempotent?
- Should the fixer be able to see other tasks in requires-intervention to spot patterns across multiple failures?

## Relationship to other drafts

- **Draft #169** (force-queue endpoint): Still useful as a human escape hatch, but the fixer agent handles most cases automatically
- **Draft #167** (refactor handle_agent_result_via_flow): The refactoring makes it easier to implement the "remember where we were in the flow" logic
- **Draft #168** (unified logging): The fixer agent needs good logs to diagnose issues — unified logging makes this much easier
- **Draft #164** (unified fail_task): `fail_task()` becomes the entry point for `requires-intervention` instead of `failed`
