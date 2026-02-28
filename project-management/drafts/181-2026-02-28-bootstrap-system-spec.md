# Bootstrap system spec with self-correction invariant

**Captured:** 2026-02-28
**Builds on:** Draft #179 (intent-driven development)

## Raw

> This is also an issue with system intent. I don't think we have anywhere that describes our expectations of the system - eg that failed is a state that a task should rarely reach. And that it should be generally self-correcting.

> Path A was the only path until recently. Path B was added after. The problem is that path B didn't replace Path A in the majority of circumstances. It was applied in a local setting as a bandaid.

## The pattern we keep hitting

The orchestrator has a recurring problem: improvements get applied locally instead of systemically. The self-correcting failure path is the clearest example:

1. **Originally**, all task failures went to `failed`. One code path, simple, terminal.

2. **Later**, someone added a better approach: `fail_task()` routes through `requires-intervention` first, gives the fixer agent a chance, and only reaches `failed` if the fixer also fails. Self-correcting.

3. **But** this improvement was applied only to the specific failure mode it was designed for (step exceptions after circuit breaker). The original path — `_handle_fail_outcome()` — continued to send agent failures directly to `failed`, untouched. Nobody generalized the improvement because nobody had written down the intent: *"the system should be self-correcting."*

4. **Result**: most task failures still go straight to `failed`. The self-correcting behaviour exists but only fires for one narrow case. Two tasks (59d65398, d4f7c809) went to `failed` because their agents ran out of turns or identified a missing prerequisite — exactly the kind of thing a fixer could handle — but they hit Path A (direct to `failed`) instead of Path B (through intervention).

This is the exact pattern draft #179 describes: intent evaporates because it's never written down. An improvement gets made with a clear design intent, but because that intent lives in someone's head rather than in a document, it doesn't propagate. The next person (or agent) who touches the code doesn't know the intent exists, so they don't apply it.

## What we need

Two things, and they're the same thing:

1. **A system spec** — a document that describes what the system is *supposed to do*, stated as behavioural invariants. Not how the code works, but what guarantees it provides.

2. **Tests derived from those invariants** — so the spec isn't just documentation but a living contract that breaks when the code violates it.

Draft #179 designed this in the abstract. This draft bootstraps it concretely, starting with the invariant we just discovered.

## First invariant: self-correcting failure

### The invariant

> **Every task failure goes through intervention before reaching `failed`.** A task should only reach the `failed` queue after at least one intervention attempt. Direct routing to `failed` without intervention is a bug.

### Why it matters

- The fixer agent can handle many failures autonomously: retry with more turns, identify missing prerequisites and requeue, fix git conflicts, simplify scope
- Human attention is expensive — `failed` tasks that could have been auto-fixed waste human time
- `failed` should mean "the system tried to self-correct and couldn't" — not "the first attempt didn't work"

### The specific code fix

`_handle_fail_outcome()` (result_handler.py:445) currently bypasses `fail_task()`:

```python
# CURRENT — goes straight to failed
def _handle_fail_outcome(sdk, task_id, task, reason, current_queue):
    if current_queue == "claimed":
        fail_target = _get_fail_target_from_flow(task, current_queue)  # returns "failed"
        sdk.tasks.update(task_id, queue=fail_target)  # direct update, no intervention
        return True
```

Should instead route through `fail_task()`, which already implements the intervention-first logic:

```python
# FIXED — goes through intervention
def _handle_fail_outcome(sdk, task_id, task, reason, current_queue):
    if current_queue == "claimed":
        fail_task(task_id, reason=reason, source="agent-failure")
        return True
```

`fail_task()` already does the right thing:
- First failure → `request_intervention()` → `requires-intervention` (fixer gets a chance)
- Second failure (fixer also failed) → `failed` (true terminal)

### The test

```python
def test_agent_failure_goes_through_intervention_not_direct_to_failed():
    """System invariant: every failure path goes through intervention first."""
    # Create a task, move to claimed
    # Simulate agent failure (outcome="failed")
    # Assert task is in requires-intervention, NOT failed
    # Assert intervention_context.json was written
```

Also a structural test (lint-level):

```python
def test_no_direct_routing_to_failed():
    """No code path should call sdk.tasks.update(queue='failed') directly.
    All failure routing must go through fail_task()."""
    # grep the codebase for sdk.tasks.update with queue="failed"
    # Only fail_task() should contain this pattern
```

## The system spec file

Bootstrap `docs/system-spec.yaml` with this first invariant and a few others we already know are true:

```yaml
# docs/system-spec.yaml
#
# Behavioural invariants for the octopoid orchestrator.
# Each entry describes what the system is supposed to do — not how the code
# works, but what guarantees it provides. Tests are derived from these
# invariants. Agents read this spec before making changes.
#
# Format:
#   id: unique identifier (kebab-case)
#   description: human-readable invariant statement
#   rationale: why this invariant matters
#   added_by: task or draft that introduced it
#   test: path to test that verifies it (null = needs test)

behaviours:
  failure-handling:
    - id: self-correcting-failure
      description: >
        Every task failure goes through intervention before reaching the failed
        queue. A task only reaches failed after at least one intervention attempt.
        Direct routing to failed without intervention is a bug.
      rationale: >
        The fixer agent can handle many failures autonomously. failed should mean
        "the system tried to self-correct and couldn't", not "the first attempt
        didn't work".
      added_by: draft-181
      test: null  # needs test

    - id: fail-task-is-the-only-path-to-failed
      description: >
        All code paths that move a task to the failed queue go through fail_task().
        No code should call sdk.tasks.update(queue='failed') directly except
        fail_task() itself.
      rationale: >
        fail_task() implements the intervention-first logic. Bypassing it creates
        paths where tasks go directly to failed without intervention, violating
        self-correcting-failure.
      added_by: draft-181
      test: null  # needs test (structural/lint)

  task-lifecycle:
    - id: worktree-preservation
      description: >
        When a task is requeued after an agent has worked on it, the existing
        worktree and commits are preserved. The agent resumes from existing work,
        not from scratch.
      rationale: >
        Destroying completed work wastes agent turns and creates frustrating
        loops where agents redo work that was already done.
      added_by: draft-175
      test: null  # needs test

    - id: step-verification
      description: >
        Flow steps are only marked as completed in step_progress.json after
        verification that the action durably took effect (e.g. branch exists
        on remote after push, PR exists after create_pr).
      rationale: >
        Without verification, steps can ghost-complete — the function returns
        without exception but the action didn't actually happen. This causes
        tasks to be marked done when their work never landed.
      added_by: draft-180
      test: null  # needs test

    - id: step-idempotency
      description: >
        Flow steps can be safely retried. Each step checks whether its work
        is already done before executing, and skips if so.
      rationale: >
        When the fixer resumes a failed flow, it re-runs from the failed step.
        If the step partially succeeded on the first attempt (e.g. branch was
        pushed), the retry must not fail because the work already exists.
      added_by: draft-180
      test: null  # needs test

  scheduling:
    - id: claim-limit
      description: >
        The scheduler never claims more tasks than max_claimed allows for
        any agent blueprint.
      rationale: >
        Overclaiming wastes resources and can cause contention. Each blueprint
        defines its capacity and the scheduler respects it.
      added_by: bootstrap
      test: null  # needs test
```

## How this connects to the agent workflow

Once the spec file exists:

1. **Implementers** read it before starting, so they know what invariants to preserve. They update it if their change introduces new behaviour.

2. **Gatekeeper** checks that the spec was updated if the diff introduces new behaviour. Checks that no invariant was removed without explanation.

3. **Codebase analyst** periodically reviews the spec for staleness — invariants whose tests don't exist, invariants that contradict the code.

4. **Testing analyst** derives integration tests from untested invariants (entries with `test: null`). This is the "QA from intent" loop.

But we don't need to build all of that now. **Step 1 is just creating the file and the first test.** The workflow integration comes later.

## Open Questions

- Should the spec live in `docs/system-spec.yaml` (single file) or `docs/behaviours/` (directory)? Start with single file, split later if it grows.
- Should agents be required to update the spec on every change, or should it be maintained by the analyst on a cadence? Start with analyst-maintained to avoid slowing down the implementation loop.
- How do we handle invariants that are aspirational vs actually enforced? Mark them differently? (e.g. `status: enforced` vs `status: aspirational`)

## Concrete next steps

1. Create `docs/system-spec.yaml` with the invariants above
2. Fix `_handle_fail_outcome()` to route through `fail_task()` — the one-line fix
3. Write the test for `self-correcting-failure` — simulate agent failure, assert task goes to `requires-intervention`
4. Write the structural test for `fail-task-is-the-only-path-to-failed` — grep for direct `queue='failed'` calls
5. Update the spec entries with test paths
6. Add the spec file to the codebase analyst's reading list so it gets reviewed periodically
