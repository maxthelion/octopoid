# Server-side state machine for task transitions

**Captured:** 2026-03-02

## Raw

> The server should have a state machine for transitions. It's just not been used properly. Every endpoint manually sets/clears its own fields — there's no shared concept of "what does a task in state X look like." Post-done hook failures conflate with pre-done flow failures because `needs_intervention` has no scope. The fixer circuit breaker can't distinguish between "fixer couldn't fix it" and "task already resolved."

## Idea

The server has 6 transition endpoints (claim, submit, accept, reject, force-queue, requeue) plus the generic PATCH. Each manually sets/clears its own list of columns. None of them know about each other's fields. This means fields that should be cleared on a transition survive by accident — `claimed_by` on submit (fixed in commit 61c0918), `needs_intervention` on accept (still broken), `lease_expires_at` on various paths.

The fix is a **transition table** on the server that declares, for each valid `from_queue → to_queue` transition, the complete set of field resets. Every transition endpoint merges its specific payload (e.g. `commits_count` for submit) with the transition table's resets. One place to audit, one place to fix.

### Transition table (proposed)

```
claimed → provisional:
  claimed_by = NULL
  claimed_at = NULL
  lease_expires_at = NULL
  orchestrator_id = NULL
  needs_intervention = FALSE

provisional → done:
  claimed_by = NULL
  claimed_at = NULL
  lease_expires_at = NULL
  orchestrator_id = NULL
  needs_intervention = FALSE
  completed_at = now()

provisional → incoming:
  claimed_by = NULL
  claimed_at = NULL
  lease_expires_at = NULL
  orchestrator_id = NULL
  rejection_count += 1

incoming → claimed:
  claimed_by = <agent>
  claimed_at = now()
  lease_expires_at = <lease>
  orchestrator_id = <orch>

* → failed:
  claimed_by = NULL
  claimed_at = NULL
  lease_expires_at = NULL
  orchestrator_id = NULL
  needs_intervention = FALSE

* → incoming (requeue):
  claimed_by = NULL
  claimed_at = NULL
  lease_expires_at = NULL
  orchestrator_id = NULL
  needs_intervention = FALSE
  attempt_count += 1
```

Each transition endpoint would call a shared `applyTransition(task, fromQueue, toQueue, payload)` function that:
1. Validates the transition is legal (reject `claimed → done` etc.)
2. Applies the field resets from the table
3. Merges the endpoint-specific payload on top
4. Bumps `version` and sets `updated_at`

The PATCH endpoint stays as the escape hatch for admin overrides (force-queue), but even it should warn or log when it creates a state the transition table wouldn't produce.

### Intervention scoping

The current `needs_intervention` boolean conflates two cases:

1. **Pre-done**: A flow step failed mid-transition. The fixer should resume the interrupted flow (re-run the failed step and complete the transition).
2. **Post-done**: The task reached done (PR merged) but a non-critical post-merge step failed (e.g. `update_changelog`). The task IS done — the fixer shouldn't re-transition it, but should handle the specific failed step.

Right now the fixer always tries to "resume the flow." For post-done failures, it finds nothing to resume and reports "already fixed", which the circuit breaker counts as an attempt. After 3 cycles, the task moves to failed — even though it was correctly done.

Options:
- **Minimal**: Add an `intervention_scope` field (`pre_transition` | `post_transition`) so the fixer knows what to do
- **Moderate**: Split into two flags — `needs_intervention` for pre-done, `needs_post_done_cleanup` for post-done
- **Structural**: Post-done steps don't use the intervention system at all. They log failures to a separate cleanup queue that a different mechanism handles (or just retries on next scheduler tick)

### Fixer circuit breaker

The circuit breaker currently counts all `intervention_reply` messages regardless of outcome. It should distinguish:
- **"fixer couldn't fix it"** → count toward the limit
- **"task already resolved"** → skip, clear `needs_intervention`, don't count
- **"systemic escalation"** → move to failed immediately, don't burn remaining attempts

## Invariants

- **transition-resets-state**: Every queue transition applies a declared set of field resets from the transition table. No field survives a transition by accident. The table is the single source of truth for what a task looks like in each queue state.
- **intervention-has-scope**: When `needs_intervention` is set, the system records whether the intervention is pre-transition (resume the flow) or post-transition (handle post-done cleanup). The fixer uses this to choose its strategy.
- **done-is-stable**: A task in `done` queue is never moved to `failed` by any automated process. Post-done step failures are handled without changing the queue.
- **fixer-checks-outcome**: The fixer circuit breaker distinguishes between failed fix attempts (count toward limit), already-resolved tasks (clear flag, don't count), and systemic escalations (fail immediately).
- **transition-is-validated**: The server rejects transitions not present in the transition table (e.g. `claimed → done`, `incoming → provisional`). Only force-queue bypasses validation.

## Context

This draft extends draft 216 (task state should be a state machine with enforced transitions) with concrete implementation details, the transition table, and the intervention scoping problem.

The immediate trigger was investigating why tasks 2eb8b79f and 35c8295c ended up in `failed` despite being approved and merged. Both had `needs_intervention=TRUE` from an earlier failure that wasn't cleared when they reached `done`. The fixer spawned against them, found nothing to fix, and the circuit breaker fired — moving correctly-done tasks to failed.

The `claimed_by` leak on submit (server task `clear-claimed-by-on-submit.md`) was the same class of bug. A full audit (this session) found 12+ locations across server endpoints and scheduler code where fields survive transitions they shouldn't.

Related:
- Draft 216: task state should be a state machine with enforced transitions
- Draft 220: robust implementer retry prompt (also involves stale state on retries)
- Postmortem 2026-03-01: `needs_intervention` not cleared on lease expiry
- Server task: `clear-claimed-by-on-submit.md`

## Open Questions

- Should the transition table live in the server code (TypeScript), in the database (a `transitions` table), or as a shared config file?
- Should force-queue also apply the transition table resets (with overrides), or remain fully manual?
- For post-done intervention: is a separate field/queue the right approach, or should the scheduler just retry post-done steps on a timer without using the intervention system?
- Should the server reject unknown transitions at the API level (400 Bad Request), or log a warning and allow them?
- Does the PATCH endpoint need to be constrained, or is it always admin-only and therefore exempt?

## Possible Next Steps

- Write the `applyTransition()` function and transition table for the server (TypeScript)
- Migrate each transition endpoint (claim, submit, accept, reject, requeue) to call `applyTransition()`
- Add `intervention_scope` field to the tasks table (or split the flag)
- Update the fixer circuit breaker to check task queue and intervention scope before counting
- Add server-side validation that rejects illegal transitions (unless force-queue)
