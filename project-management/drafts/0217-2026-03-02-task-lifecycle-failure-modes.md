# Task lifecycle failure modes: what actually happens at each stage

**Captured:** 2026-03-02

## Raw

> Is the following broadly complete for the first part of the process? A task is defined and put in incoming. A task is claimed (with a lease) and given to an agent. An agent is given a task and works on it until completion and writes to stdout. An agent commits work to their branch as they proceed. A scheduler looks for work done, and expires lease and cleans up pids, creates PR, rebases and transitions task to provisional.
>
> Failure modes for this section of the workflow: problem creating worktree/claiming/spawning, agent interrupted and never writes stdout, agent doesn't commit changes, agent can't finish in turns allowed, scheduler interrupted during cleanup steps (expire lease, create PR, rebase, transition).

## Happy Path

1. A task is created and placed in `incoming`
2. The scheduler claims it (with a lease) and spawns an agent in a worktree
3. The agent works on the task — reading code, making changes, committing to its branch, writing to stdout
4. The agent exits (completes or hits turn limit)
5. The scheduler detects the dead PID (every 10s via `check_and_update_finished_agents`)
6. Haiku reads stdout and infers the outcome (`done`, `failed`, `unknown`, `needs_continuation`)
7. On `done`: the scheduler runs flow steps in order — `push_branch` → `run_tests` → `create_pr` → transition to `provisional`
8. The gatekeeper claims from `provisional`, reviews the PR, and approves or rejects

Note: the agent does NOT create the PR or push its branch. The scheduler runs those as post-completion flow steps after inferring the outcome. The agent's only job is to write code, commit, and exit.

## Failure Modes

### 1. Problem creating worktree, claiming lease, or spawning the agent

**What actually happens:** The spawn wrapper (`scheduler.py:2359`) catches the exception, logs it, and calls `_requeue_task()` which puts the task back in `incoming` with `attempt_count` incremented. After 3 spawn failures, the circuit breaker moves it to `failed` with source `spawn-failure-circuit-breaker`.

**Result:** Not silent. The task bounces back to incoming and gets retried automatically. If it keeps failing, it reaches `failed` after 3 attempts. The root cause is only in the scheduler log, not on the task itself.

**Assessment:** This is wrong. A spawn failure is almost never the task's fault — it's a systemic issue (git broken, disk full, agent binary missing, resource exhaustion). Failing the task hides the real problem and the next task will hit the same issue. The task should go back to incoming *without incrementing attempt_count*, and if the same failure happens on the next attempt, the system should pause intake. The current behaviour — retry 3 times then fail — burns through the queue while the underlying issue persists.

### 2. Agent interrupted — never completes, never writes to stdout

**What actually happens:** Two sub-cases:

**Agent process dies (PID no longer running):** `check_and_update_finished_agents` finds the dead PID, reads the empty stdout.log, haiku infers `outcome=unknown` ("Empty stdout — agent may have crashed"), and `request_intervention()` is called — sets `needs_intervention=True` and posts an intervention request message. A fixer agent is spawned to investigate.

**Agent process hangs (PID still alive but doing nothing):** The PID stays in `running_pids.json` and is never processed because `is_process_running()` returns True. The lease eventually expires (default 60 min) and `check_and_requeue_expired_leases` moves the task back to incoming. But the orphan process keeps running, consuming resources and potentially blocking the pool.

**Result:** Dead process → intervention → fixer. Hung process → lease expiry → requeue, but orphan persists.

**Assessment:** The dead process path works correctly. The hung process path has a known bug: the orphan PID is never killed, and if `needs_intervention` was set before the lease expired, it leaks through to the requeued task (fixed 2026-03-01 for the flag leak, but orphan killing is still missing). The system should kill the PID when the lease expires.

### 3. Agent doesn't commit changes (or commits partially)

**What actually happens:** The agent writes to stdout saying it's done (or not). Haiku infers the outcome.

**If outcome=done:** The scheduler runs flow steps: `push_branch` pushes whatever was committed (possibly nothing), `create_pr` creates a PR (possibly with no diff), and the task transitions to `provisional`. The gatekeeper reviews the PR and sees empty/partial work → rejects → task goes back to incoming with the worktree preserved for another attempt.

**If outcome=failed/unknown:** Goes to intervention as described above.

**Result:** This works as designed. The gatekeeper is the quality gate for LLM output. An agent that claims to be done but didn't commit anything will have its empty PR rejected. The non-deterministic LLM output is evaluated by another LLM — that's the correct place to make the judgment call.

**Assessment:** Sound. The gatekeeper rejection preserves the worktree so the next attempt can build on partial work. The only concern is that creating an empty PR is wasteful — a pre-check on "does the branch have any commits beyond base?" before `create_pr` would avoid the noise.

### 4. Agent can't finish in the turns allowed

**What actually happens:** Claude exits when it hits the turn limit. Stdout contains whatever the agent wrote up to that point. Haiku infers the outcome:

- If the agent wrote a summary before exiting → `outcome=done` → flows through steps → provisional → gatekeeper evaluates incomplete work → likely rejects → back to incoming with worktree preserved
- If the agent was mid-sentence → `outcome=unknown` → intervention → fixer
- If the agent wrote "I ran out of turns" → `outcome=needs_continuation` → moved to `needs_continuation` queue (but this queue currently has no consumer, so the task sits there)

**Result:** The gatekeeper rejection path is the primary mechanism. The task comes back with the worktree intact, so progress is preserved. If consistently hitting the turn limit, the task description needs to be scoped smaller — that's a task writing problem, not an orchestrator problem.

**Assessment:** The `needs_continuation` path is the most interesting. The agent is saying "I made progress but I'm not done" — this is valuable signal. Currently this goes nowhere because nothing claims from `needs_continuation`. This should be a first-class path: re-spawn the agent with the same worktree and a "continue where you left off" prompt.

### 5. Scheduler interrupted during cleanup steps

Each step has pre_check/execute/verify phases and progress is tracked in `step_progress.json`. All step failures go through the same retry → circuit breaker path:

- Step fails → exception caught by `handle_agent_result`
- `RetryableStepError` → PID kept in tracking, retry on next scheduler tick (10s)
- Other exception → `step_failure_count` incremented, retry on next tick
- After 3 consecutive failures → task moved to `failed` with source `step-failure-circuit-breaker`

Specific scenarios:

**Cannot push branch (network error, auth failure):**
`push_branch` step fails → retries next tick. Transient network issues resolve themselves. Auth failures won't — 3 retries then failed. The step doesn't distinguish between transient and permanent failures.

**Cannot create PR (GitHub API error):**
`create_pr` step fails → retries. The `pre_check` is smart: if a PR already exists for this branch, it skips creation and stores the PR number. So if the PR was created but the response was lost, the next retry finds it.

**Cannot rebase because of conflicts:**
`rebase_on_base` step aborts the rebase on conflict and raises `RetryableStepError`. Retries next tick. But a merge conflict won't resolve itself — this wastes 3 retries before hitting the circuit breaker and moving to `failed`.

**Cannot transition to provisional (server error):**
`_perform_transition` calls `sdk.tasks.submit()` which fails → exception → retry next tick. Again, transient server errors resolve; persistent ones hit the circuit breaker.

**Assessment:** The retry mechanism is correct for transient issues (network blip, momentary server error). The key gap is that merge conflicts and permanent errors go through the same 3-retry path as transient ones. The code has `PermanentStepError` for "this needs human intervention immediately" but `rebase_on_base` doesn't use it for conflicts — it uses `RetryableStepError`, wasting retries on something that will never self-resolve.

## Key Gaps Identified

### 0. No distinction between task-scoped and systemic failures
This is the most important gap. When a failure is caused by something outside the task itself — worktree creation broken, git auth expired, agent binary missing, server unreachable — the current system fails the individual task and moves on to the next one, which hits the same problem. This is the worst possible response: it burns through the queue, marks good tasks as failed, and hides the systemic issue behind a pile of individual failures.

The correct response to a systemic failure is to **pause the system**, not fail the task. The task is fine — the infrastructure is broken. Failing the task punishes the task for a problem it didn't cause and makes recovery harder (now you have N failed tasks to re-enqueue instead of 0).

Systemic failures include:
- Worktree creation failures (git, filesystem)
- Agent spawn failures (binary missing, permissions, resource exhaustion)
- Server connectivity failures (API unreachable, auth expired)
- GitHub API failures (rate limited, token expired)
- Step failures that aren't task-specific (rebase infra broken, PR creation broken)

Task-scoped failures include:
- Merge conflicts (this branch conflicts with base — specific to this task's changes)
- Agent couldn't do the work (LLM output quality — specific to this task's description)
- Empty description (this task was created wrong)
- Test failures (this task's code broke tests)

The system should detect when the same failure hits 2+ tasks in a row and auto-pause intake rather than continuing to fail tasks. A single spawn failure should put the task back in incoming *without incrementing attempt_count* — because it wasn't the task's fault. If the same failure happens again on the next claim attempt, that's the signal to pause.

### 1. No distinction between transient and permanent step failures
Merge conflicts, auth failures, and missing permissions should use `PermanentStepError` to skip the retry loop and go straight to intervention. Currently everything retries 3 times.

### 2. Orphan processes not killed on lease expiry
When a lease expires, the task is requeued but the original agent process keeps running. No code kills the PID. This wastes resources and can cause conflicts if the task is re-claimed.

### 3. `needs_continuation` queue has no consumer
An agent saying "I made progress but ran out of turns" is valuable signal that goes nowhere. This should re-spawn with "continue" instructions and the same worktree.

### 4. No pre-check for empty branches before PR creation
If the agent committed nothing, `push_branch` + `create_pr` creates an empty PR that the gatekeeper has to review and reject. A simple "are there commits beyond base?" check would skip PR creation and go straight to rejection.

### 5. Step failure root cause not stored on the task
When a step fails 3 times and hits the circuit breaker, the `source` tag says `step-failure-circuit-breaker` but doesn't say *which* step failed or *why*. `step_progress.json` has this info locally but it's not propagated to the task on the server.

## Invariants

- **systemic-failures-pause-not-fail**: When a failure is not task-scoped (spawn failure, server unreachable, git broken, auth expired), the system pauses intake rather than failing the task. The task is not at fault and should not be penalised. Failing individual tasks for systemic issues hides the real problem and burns through the queue.
- **task-scoped-vs-systemic-detected**: The system distinguishes between task-scoped failures (merge conflict, empty description, LLM couldn't do the work) and systemic failures (infrastructure broken, external service down). If the same non-task-scoped failure hits 2+ tasks in a row, the system auto-pauses.
- **transient-vs-permanent-step-errors**: Steps distinguish between transient failures (retry) and permanent failures (escalate immediately). Merge conflicts, auth failures, and permission errors use `PermanentStepError`. Network timeouts and API rate limits use `RetryableStepError`.
- **orphans-killed-on-lease-expiry**: When a task's lease expires and is requeued, the original agent PID is killed if it's still running. No orphan processes persist after lease expiry.
- **needs-continuation-is-handled**: The `needs_continuation` queue has a consumer that re-spawns the agent with the same worktree and a "continue" prompt. Agent progress is preserved across continuation cycles.
- **empty-work-detected-before-pr**: Before creating a PR, the system checks whether the branch has commits beyond the base. If not, the task is rejected without creating an empty PR.
- **step-failure-detail-on-task**: When a task fails due to a step error, the failing step name and error message are stored on the task (not just locally in `step_progress.json`), so the diagnostic agent and dashboard can show what went wrong without reading local files.

## Context

This draft documents the actual behaviour of the first phase of the task lifecycle (incoming → claimed → agent works → provisional) by tracing the code paths. It was prompted by task 868b0322's failure, where the intervention flag leaked through lease expiry causing dual agent claims.

Related: draft 216 (codify failure modes — the broader argument that most "failures" are bugs), draft 210 (diagnostic agent), postmortem 2026-03-01-task-868b-intervention-leak.

## Possible Next Steps

- Add systemic failure detection: if spawn or step failure is not task-specific, don't increment attempt_count — pause intake instead. Track "consecutive non-task failures" as a system-level metric.
- Fix `rebase_on_base` to use `PermanentStepError` for merge conflicts (one-line change)
- Add PID killing to `check_and_requeue_expired_leases` when moving tasks from claimed → incoming
- Add a `needs_continuation` consumer to the scheduler that re-spawns agents with "continue" instructions
- Add a "branch has commits?" pre-check before `create_pr`
- Propagate `step_progress.json` content to task `execution_notes` on circuit breaker failure
- Classify every current failure path as task-scoped or systemic — systemic ones should never touch `attempt_count`
