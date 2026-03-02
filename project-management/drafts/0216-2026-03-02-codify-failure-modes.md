# Enumerate and codify failure modes: replace ad-hoc fixes with a testable failure taxonomy

**Captured:** 2026-03-02

## Raw

> Looking at the list above, and the 9 points we gave to the fixer about how a task can end up in failed, I can't help thinking that we need a more robust mechanism for classifying failure modes and moving things around. It always seems like we are making untestable updates that are likely to break something else. There must be some patterns that we can codify, rather than the combinatorial mess that we have at the moment. Things like pids existing for non-existing claims, failed tasks with no stdout, lack of commits, wrongly labelled interventions. There should be very few scenarios that actually need to get to failed. These include things like: human judgment needing to be exercised (eg a conflict that requires decisions), or mismatch of functionality with server.

## Idea

Most of the 9 routes to `failed` aren't failure modes — they're bugs in our orchestrator. We keep building elaborate recovery machinery (circuit breakers, fixer agents, intervention flags, diagnostic agents) to gracefully handle situations that shouldn't exist. Orphan PIDs, leaked `needs_intervention` flags, missing `_perform_transition` calls, state inconsistencies — these are defects in deterministic code that we control. The correct response is to fix the code, not to build ever-more-sophisticated failure recovery around it.

The one genuinely non-deterministic part of the system is the LLM agent doing the work. We describe a task, hand it to an external model, and the outcome is uncertain. The agent might produce great code, or it might misunderstand the task, take the wrong approach, run out of turns, or produce something that doesn't meet the acceptance criteria. That's where we need flexibility, judgment, and graceful handling — because we can't eliminate the uncertainty.

### Two fundamentally different problem spaces

**Things within our control (fix, don't handle):**

The orchestrator is deterministic software. When it misbehaves, the answer is a bug fix plus a test, not a recovery mechanism. Every one of these has been treated as a "failure mode" when it's actually a bug:

- Orphan PIDs existing for non-existent claims → bug in PID lifecycle management
- `needs_intervention` leaking through lease expiry → bug in state transition (fixed 2026-03-01)
- `_perform_transition` not called after gatekeeper approve → bug in result handler (fixed via PR #289)
- Stale PIDs blocking the pool → bug in cleanup logic
- Two agents claiming the same task → bug in guard logic
- Steps marked complete but task not advancing → bug in step execution

These should each be a bug fix with an integration test that prevents regression. The circuit breakers and fixer agents are compensation for code we haven't fixed yet. As we fix the underlying bugs, the recovery machinery should shrink, not grow.

**Things outside our control (real failures — handle gracefully):**

These are the situations the failure handling system should actually be designed for:

- **LLM agent output** — The agent might misunderstand the task, write code that doesn't compile, take the wrong approach, or produce something that doesn't satisfy the acceptance criteria. This is inherent to using a non-deterministic external model and can't be eliminated. This is where we need the most flexibility and judgment — evaluating whether the work achieved its intent, deciding whether to retry with different instructions, or escalating when human judgment is genuinely needed.
- **Network/infrastructure** — Server unreachable, GitHub API rate limits, CI failures, Anthropic API outages, agent process killed by OOM. These are real external failures that happen to working code. The correct response is usually to wait and retry — the system should handle these without human intervention.
- **Genuine conflicts** — Merge conflicts requiring design decisions, task requirements that conflict with the current codebase, work that depends on server features that don't exist yet. These need human judgment because the system can't know what the right answer is.

### Where the effort should go

The fixer agent and diagnostic agent (draft 210) should focus almost entirely on the non-deterministic space — evaluating LLM output quality, deciding if a retry with adjusted instructions would help, spotting when a task description is ambiguous or contradicts the codebase. That's hard and valuable work.

They should NOT be spending their time compensating for bugs in our own state management. Every time the fixer encounters "the work is done but the task didn't transition", that's our bug, not theirs.

### The state machine problem

The deeper issue is that task state is a bag of independently-settable fields with no enforced transitions. Any code path can set `queue`, `needs_intervention`, `claimed_by`, `attempt_count`, and `lease_expires_at` in any combination. This makes it trivial to create impossible states (e.g. `queue=incoming` + `needs_intervention=True` + `claimed_by=implementer`).

A proper state machine would define:
- The valid states (each is a specific combination of queue + flags)
- The valid transitions between states
- What each transition clears/sets

Invalid transitions would be rejected, making it impossible to create the contradictory states that cause most of our "failure modes".

## Invariants

- **orchestrator-bugs-are-fixed-not-handled**: When the orchestrator's own deterministic code produces an invalid state, the response is a bug fix with a regression test — not a recovery mechanism. Circuit breakers and fixer agents exist for non-deterministic failures, not for compensating our own bugs.
- **bugs-pause-the-line**: When an orchestrator bug is identified that causes tasks to enter invalid states, the system pauses intake until the fix is deployed and tested. Running tasks through a broken pipeline creates more mess than stopping to fix it. A paused system with a fix shipping is better than an active system accumulating failures.
- **task-state-machine-enforced**: Task state transitions are defined as a state machine with valid states and valid transitions. Invalid transitions are rejected. It is not possible for code to create contradictory state combinations (e.g. `needs_intervention=True` in the wrong queue).
- **failure-handling-focuses-on-real-uncertainty**: The fixer agent, diagnostic agent, and intervention system focus on genuinely uncertain outcomes — LLM agent output quality, network/infrastructure failures, and conflicts requiring human judgment. They do not spend effort on orchestrator state management bugs.
- **deterministic-paths-have-tests**: Every deterministic code path in the orchestrator (claim, spawn, result handling, step execution, queue transitions) has an integration test. If it can be tested, it should be tested — failure recovery is not a substitute for correctness.

## Context

Of the 9 routes to `failed` documented in draft 210:

**Bugs (fix the code, pause if necessary):**
- Route 3 (fixer resume error) — bug in worktree/branch state management
- Route 5 (flow dispatch error) — bug in step/flow system
- Route 7 (spawn failure circuit breaker) — bug in spawn/worktree setup
- Route 8 (empty description) — validation gap at task creation time
- Routes 1-2 (fixer circuit breaker, fixer failed) — exist primarily because the fixer is compensating for other orchestrator bugs rather than evaluating LLM work

**Real failures (handle gracefully):**
- Route 4 (step failure) — could be a transient network issue (GitHub API down during merge step) or a genuine conflict
- Route 6 (lease expiry) — agent genuinely hung, or network dropped, or machine ran out of resources
- Route 9 (agent failure via fail_task) — the LLM agent couldn't do the work. This is the core non-deterministic case

The bulk of the recovery machinery is compensating for bugs. The real failures (network, LLM output, conflicts) are relatively few and are the ones worth building intelligent handling for.

Related: spec capabilities 1.13 (failure surfaces early and heals), 1.7 (circuit breakers stop loops), draft 210 (diagnostic agent), postmortem 2026-03-01-task-868b-intervention-leak, postmortem 2026-03-01-gatekeeper-approve-no-transition.

## Open Questions

- What does the task state machine look like? How many valid states are there, and what are the transitions?
- Should state machine enforcement live in the server API (reject invalid transitions) or in the orchestrator (validate before calling the API)?
- How do we migrate from the current bag-of-fields model to an enforced state machine without breaking everything at once?
- For the LLM output evaluation problem (the genuinely hard part): what signals does the fixer use to decide between "retry with same instructions", "retry with adjusted instructions", and "escalate to human"?

## Possible Next Steps

- Map out the valid task states and transitions as a state machine diagram
- Audit the 19 tasks currently in `failed` — classify each as "orchestrator bug" vs "LLM output problem" vs "genuine conflict"
- Fix the top 3 most common orchestrator bugs that put tasks into failed, with integration tests
- Refocus the fixer agent's prompt on LLM output evaluation rather than orchestrator state recovery
- Design the state machine enforcement layer (server-side vs client-side)
