# Diagnostic agent for the failed queue: post-mortems, pattern detection, and autonomous resolution

**Captured:** 2026-03-01

## Raw

> We are working on this at the moment in terms of observability. Where we are lacking is that we currently babysit the failed queue ourselves and manually intervene. We should have another diagnostic agent that performs a post-mortem on issues that get to failed, can read previous results to see patterns, and can schedule work to improve the system. They should resolve tasks that are failed by taking appropriate action to either force it through, cancel it, re-enqueue (with or without work trees) etc.

## Idea

A diagnostic agent monitors the failed queue. For each failed task, it performs a post-mortem: reads the task's message history, error context, step progress, and previous fixer attempts. It diagnoses the root cause and takes action — re-enqueue the task (with or without its worktree), force it through to done, or cancel it.

Beyond individual tasks, it reads across failures to spot patterns. If the same flow step keeps failing for multiple tasks, it proposes a draft to fix the root cause rather than patching individual tasks. This is the difference between treating symptoms and treating causes.

The current state is that the circuit breaker moves tasks to failed after 3 fixer attempts, and then a human has to investigate each one manually. The diagnostic agent replaces the human in this loop.

## Invariants

- **diagnostic-agent-handles-failed**: A diagnostic agent monitors the failed queue. For each failed task it performs a post-mortem and takes action (re-enqueue, force through, cancel) rather than leaving it for a human.
- **diagnostic-agent-spots-patterns**: The diagnostic agent reads previous failure results across tasks to identify recurring patterns. When it spots a systemic issue, it schedules work to fix the root cause.
- **failures-have-recorded-reasons**: Every task that enters requires-intervention or failed has a recorded diagnosis of what went wrong, what was tried, and why it couldn't be resolved.

## Context

Today's investigation of task 2b09a4db is a perfect example: the fixer ran 3 times, correctly diagnosed "already complete", but couldn't fix the underlying issue (missing `_perform_transition` call). A diagnostic agent would have read the pattern — "fixer says fixed but task doesn't advance" — and either forced the task to done or filed a bug report.

Related: spec capability 1.13 ("Failure is surfaced early and noisily and automatically heals"), observability/self-healing, postmortem 2026-03-01-gatekeeper-approve-no-transition.

## Routes to Failed

There are 9 distinct code paths that move a task to the `failed` queue. Each has a `source` tag for identification. The diagnostic agent's prompt should include this list so it can identify which route caused the failure and apply the right heuristic.

### 1. `fixer-circuit-breaker` — scheduler.py:272
Fixer has been attempted 3 times and the task still hasn't recovered. The scheduler moves it directly to `failed` via `sdk.tasks.update(queue="failed")`.
**Evidence:** `fixer_attempts >= 3` in task metadata. Message history will show 3 fixer role results.

### 2. `fixer-failed` — result_handler.py:1118
The fixer agent explicitly reported that it could not fix the issue (returned `non-fixed` outcome).
**Evidence:** Last message from fixer role with `result_type: non-fixed` or similar.

### 3. `fixer-resume-error` — result_handler.py:1081
The fixer agent was supposed to resume but the resume process itself crashed (e.g. worktree missing, branch gone).
**Evidence:** Error traceback in execution_notes or messages mentioning resume failure.

### 4. `step-failure-circuit-breaker` — result_handler.py:885
A flow step failed 3 consecutive times. This is distinct from fixer circuit breaker — it's about post-merge steps (changelog, rebase, etc.) failing repeatedly.
**Evidence:** `step_progress` in task metadata shows a step with `consecutive_failures >= 3`.

### 5. `flow-dispatch-error` — result_handler.py:805
The flow dispatch mechanism itself crashed — couldn't determine which step to run, couldn't parse the flow YAML, etc.
**Evidence:** Error traceback in messages mentioning flow dispatch. Often a code bug rather than a task-specific issue.

### 6. `lease-expiry-circuit-breaker` — scheduler.py:1832
A task's lease expired repeatedly (agent claimed it but never reported back). After hitting the limit, the scheduler moves it to failed.
**Evidence:** Multiple lease expiry events in messages. Agent may have crashed, hung, or lost connectivity.

### 7. `spawn-failure-circuit-breaker` — scheduler.py:2185
The scheduler tried to spawn an agent for this task multiple times and it kept failing (e.g. subprocess crash, missing agent config, resource limits).
**Evidence:** Spawn error messages in task history. No agent result messages at all.

### 8. `guard-empty-description` — scheduler.py:371
Task was created with an empty or missing description. The guard catches this before any agent runs.
**Evidence:** Task content/description is empty. No agent messages.

### 9. Agent failure via `fail_task()` — result_handler.py:445 → tasks.py:420
An agent (implementer, gatekeeper, etc.) returned a failure outcome. `fail_task()` uses two-stage routing: first failure sets `needs_intervention=True` (dispatching a fixer), second failure moves to `failed`.
**Evidence:** Agent result messages showing failure. If `needs_intervention` was already true when the second failure hit, both the original agent and fixer failed.

## Diagnostic Heuristics

The diagnostic agent reads the task's `source` tag (if present), message history, step_progress, and execution_notes, then applies the appropriate heuristic. If the failure doesn't match a known route, it escalates to a human.

### Force through to done
- **fixer-circuit-breaker** where fixer messages say "already complete" or all steps show `completed` in step_progress — the work is done, the transition just didn't happen.
- **step-failure-circuit-breaker** where the failing step is non-critical (e.g. changelog update) and the core work (PR merged, code landed) is verified.

### Re-enqueue without worktree
- **spawn-failure-circuit-breaker** — worktree may be corrupted or missing. Fresh start is safest.
- **lease-expiry-circuit-breaker** — agent hung or crashed. Worktree state is unknown. Clean re-enqueue avoids inheriting a broken working directory.
- **fixer-resume-error** — resume failed because the worktree/branch is in a bad state. Fresh start.

### Re-enqueue with worktree
- **lease-expiry-circuit-breaker** where the worktree exists and has meaningful commits — worth preserving progress.
- **Agent failure** (route 9) on first occurrence where the fixer could have fixed it but hit an unrelated issue — retry with existing work.

### Cancel
- **guard-empty-description** — task is malformed. Cancel and re-create properly.
- Any task where the underlying intent is no longer relevant (branch deleted, PR closed, superseded by other work).

### Enqueue fix task
- **flow-dispatch-error** — this is almost always a code bug, not a task problem. Diagnose the bug, write a description, and enqueue a task to fix it.
- **step-failure-circuit-breaker** where the failing step has a code bug (e.g. the step script itself is broken). Same as above.
- **fixer-circuit-breaker** where the pattern repeats across multiple tasks — systemic issue, not task-specific.

### Escalate to human
- Failure doesn't match any known route or the diagnostic is ambiguous.
- Multiple heuristics could apply and the agent can't determine which is correct.
- The failure involves data integrity concerns (e.g. task claims to be done but PR was never merged).

## Diagnostic Script

The agent should have a diagnostic script (or SDK calls in its prompt) that gathers context before reasoning:

```
1. Read task metadata: queue, status, needs_intervention, step_progress, flow, execution_notes
2. Read task messages: all messages in chronological order (agent results, fixer results, system messages)
3. Check for source tag: task metadata or last system message should indicate which route
4. Check worktree: does .octopoid/runtime/tasks/<id>/worktree exist? Is it clean? Any commits?
5. Check PR: if a PR exists, what's its merge status? Any review comments?
6. Check other recent failures: are there tasks with the same source tag or same failing step?
```

This information feeds the heuristic decision. The agent's prompt should include the route table and heuristics above so it can match the evidence to the right action.

## Resolved Questions

- **Trigger mechanism:** The scheduler spawns the diagnostic agent on demand when a task enters the `failed` queue. Not a scheduled job — event-driven.
- **Postmortems:** The diagnostic agent writes postmortems automatically to `project-management/postmortems/`. Every diagnosed failure gets a recorded explanation.
- **Enqueuing work:** The diagnostic agent can enqueue tasks via the SDK when it identifies a code bug or systemic issue as the root cause.
- **Tools:** Messages API (read task history), SDK (re-enqueue, cancel, force-move, enqueue new tasks), git (inspect worktrees), gh CLI (check PR status), filesystem (write postmortems).

## Possible Next Steps

- Write the agent configuration (agent.yaml) with the diagnostic role
- Write the prompt template including the 9 failure routes and heuristic table
- Write the diagnostic script that gathers task context
- Add a scheduler hook: when a task moves to `failed`, spawn the diagnostic agent
- Start with a simple version that handles the 3 most common routes (fixer-circuit-breaker, spawn-failure, lease-expiry) and escalates everything else
