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

## Open Questions

- What tools does the diagnostic agent have? It needs to read task history (messages API), inspect worktrees (git), and take actions (re-enqueue, cancel, force-move via SDK)
- Should it run as a scheduled job or be triggered when a task enters failed?
- How does it decide between re-enqueue, force-through, and cancel? What heuristics?
- Should it write postmortems automatically, or just propose drafts?

## Possible Next Steps

- Define the diagnostic agent's action vocabulary: what operations can it perform on failed tasks?
- Write the agent configuration (agent.yaml, scripts, prompt)
- Start with a simple version: read the last fixer result, decide re-enqueue vs cancel, post a message explaining why
