# Invariant verification: mapping invariants to tests, analysts, and observability checks

**Captured:** 2026-03-01
**Author:** human + claude

## Raw

> The system spec has a `test:` field on each invariant but only 2 of 40 are filled in. We need a systematic way to verify invariants — integration tests for most, analyst grading for architectural ones, and a cross-referencing approach for observability. The viewer should show verification status per invariant.

## The Problem

The system spec says what the system should guarantee. But there's no mechanism to check whether those guarantees actually hold. The `test:` field is aspirational — it exists on every invariant but is almost always `null`.

Without verification:
- Invariants regress silently (the ghost completion incident violated `step-verification` for weeks)
- "Enforced" status is self-declared, not proven
- New code can break invariants with no signal
- The spec becomes documentation that drifts from reality

## Three verification mechanisms

Not all invariants are the same kind of claim. They need different verification approaches:

### 1. Integration tests (most invariants)

The majority of invariants describe observable system behaviour: "when X happens, Y is true." These map directly to integration tests.

```yaml
# tasks/resilience.yaml
- id: self-correcting-failure
  test: tests/integration/test_failure_routing.py::test_failure_routes_to_intervention
  verification: test
```

The test proves the invariant holds. If the test passes, the invariant is enforced. If the test doesn't exist, the invariant is aspirational regardless of what the status field says.

**What this means for the spec:** An invariant can only be `status: enforced` if `test:` points to a passing test. The viewer (or a CI check) can verify this mechanically.

**Coverage target:** Every invariant with `verification: test` should have a corresponding integration test. The viewer shows the gap.

### 2. Analyst grading (architectural invariants)

Some invariants describe qualities, not behaviours: "agents are pure functions," "prefer simple over clever," "no duplicated config." These can't be tested with assertions — they require judgement.

The analyst agents already grade facets of the codebase periodically (complexity, test coverage, copy-paste). Linking analyst scores to specific invariants creates accountability:

```yaml
# architecture/complexity.yaml
- id: reduce-code-complexity
  description: >
    Prefer simple implementations. Cyclomatic complexity of any function
    should not exceed 15. Duplicated code blocks should be under 3%.
  verification: analyst
  analyst: architecture-analyst
  metric: complexity_score
  threshold: 7  # minimum health score to consider "met"
```

The analyst's health score becomes the invariant's verification signal. If the score drops below the threshold, the invariant is flagged as regressing.

**What this means for the viewer:** Instead of green/red, architectural invariants show the analyst's latest score and trend (improving/declining/stable).

**What this means for analysts:** Their grading criteria should reference specific invariants. When an analyst proposes a draft, it should note which invariants the proposal would improve.

### 3. Observability cross-referencing (observability invariants)

Observability invariants are meta — they say "the system should provide visibility into X." Verifying them requires checking that the visibility mechanisms exist and work. Some can be tested directly, others need cross-referencing with other invariants.

The key insight: **observability invariants are satisfied when other invariants produce auditable evidence.** For example:

- "All state transitions are logged" is satisfied if every invariant about state transitions (self-correcting-failure, intervention-no-queue-transition, etc.) produces log entries or messages that can be queried.
- "Failed tasks have recorded reasons" is satisfied if `failure-reason-always-recorded` has a test that also checks the reason is queryable from the dashboard/API.

This creates cross-references between invariants:

```yaml
# observability/task-audit.yaml
- id: transitions-are-auditable
  description: >
    Every task state transition (incoming→claimed, claimed→provisional, etc.)
    produces a message or log entry that records: timestamp, from-state,
    to-state, actor, and reason. The full transition history of any task
    can be reconstructed from messages alone.
  verification: cross-reference
  depends_on:
    - self-correcting-failure      # failure → intervention transition logged
    - intervention-no-queue-transition  # intervention is a flag, not a transition
    - flow-sync-preserves-steps    # steps are recorded in transitions
  test: tests/integration/test_audit_trail.py::test_transition_history_complete

- id: step-outcomes-visible
  description: >
    For any task, the full list of steps that ran and their individual
    outcomes (success, skipped via pre_check, failed with error) can be
    retrieved. step_progress.json is written during execution and
    queryable after completion.
  verification: cross-reference
  depends_on:
    - step-verification        # steps have verify phase
    - step-idempotency         # pre_check outcomes recorded
    - step-error-classification  # error types are distinguishable
  test: tests/integration/test_step_audit.py::test_step_outcomes_queryable

- id: failure-knowledge
  description: >
    When a task fails, the system has complete knowledge of what happened:
    which step failed, what error occurred, what the fixer attempted, and
    why it ultimately couldn't recover. This knowledge is available via
    messages and task metadata without needing to read log files or
    inspect worktrees.
  verification: cross-reference
  depends_on:
    - failure-reason-always-recorded
    - intervention-context-in-messages
    - fixer-circuit-breaker
    - step-error-classification
  test: tests/integration/test_failure_knowledge.py::test_failed_task_has_full_context
```

Cross-referenced invariants are verified by:
1. Their own test (if they have one)
2. The tests of the invariants they depend on (all must pass)
3. The cross-reference relationship itself (does the evidence from dependency invariants flow into the observability claim?)

## New observability invariants to add

The user identified several observability guarantees that aren't in the current spec:

### Task failure knowledge
```yaml
- id: failure-knowledge
  description: >
    When a task fails, the system has complete knowledge: which step failed,
    what error, what the fixer tried, and why recovery failed. Available via
    messages and task metadata — no log file forensics required.
```

### Transition audit trail
```yaml
- id: transitions-are-auditable
  description: >
    Every task state transition produces an auditable record (message or log)
    with timestamp, from-state, to-state, actor, and reason. The full history
    of any task can be reconstructed from messages.
```

### Step outcome visibility
```yaml
- id: step-outcomes-visible
  description: >
    For any task, the complete list of steps that ran and their outcomes
    (success, skipped, failed + error) is retrievable. This is available
    via step_progress.json during execution and via task messages after
    completion.
```

### Intervention audit trail
```yaml
- id: intervention-history-complete
  description: >
    The full intervention lifecycle — trigger, context, fixer attempts,
    resolution or escalation — is captured in the message thread. A human
    viewing a failed task can read the thread and understand exactly what
    happened without checking scheduler logs.
```

## Verification in the viewer

The viewer (from draft 199) should show verification status per invariant:

| Status | Badge | Meaning |
|--------|-------|---------|
| Tested | Green | Has a passing integration test |
| Analyst-graded | Blue + score | Linked to analyst, shows latest score and trend |
| Cross-referenced | Purple | Verified via dependent invariants |
| Untested | Amber | Invariant stated but no verification exists |
| Failing | Red | Test exists but is currently failing |

The stats summary becomes: "12 tested, 3 analyst-graded, 5 cross-referenced, 20 untested, 0 failing"

### Verification CI job

A scheduled job (or a manual `/verify-spec` skill) that:
1. Runs all tests tagged with invariant IDs
2. Checks analyst scores against thresholds
3. Resolves cross-references (are all dependencies verified?)
4. Updates the viewer data with results
5. Posts a summary message if any enforced invariant is now failing

This could be a background agent job that runs daily, or a pre-merge CI check that runs on PRs touching code related to specific invariants.

## How invariants get linked to tests

When writing a test for an invariant:

```python
import pytest

@pytest.mark.invariant("self-correcting-failure")
def test_failure_routes_to_intervention(scoped_sdk):
    """Verify: Every task failure goes through intervention before failed."""
    # ... test implementation
```

The `@pytest.mark.invariant` marker lets the verification job discover which tests cover which invariants. The build script for the viewer can parse pytest markers to populate the test field automatically.

Alternatively, a simpler approach: just maintain the `test:` field in the YAML manually and have the verification job check that the referenced test exists and passes.

## Invariants

- `invariant-verification-exists`: Every invariant has a `verification` field indicating how it's verified: `test`, `analyst`, `cross-reference`, or `none`. Invariants with `verification: none` are explicitly flagged as unverified gaps.
- `enforced-requires-proof`: An invariant can only have `status: enforced` if its verification mechanism confirms it. For `test` verification, the test must exist and pass. For `analyst` verification, the latest score must meet the threshold. For `cross-reference`, all dependencies must be verified.
- `verification-is-visible`: The spec viewer shows verification status per invariant with colour-coded badges. The gap between stated invariants and verified invariants is always visible.
- `regression-is-detected`: When a previously-verified invariant starts failing (test breaks, analyst score drops, dependency regresses), the system produces a notification. Regressions do not go unnoticed.

## Open Questions

- Should the `@pytest.mark.invariant` approach be used, or is manual `test:` field maintenance simpler and sufficient?
- Should verification run in CI (blocking PRs) or as a background job (advisory)?
- For analyst-graded invariants, what's the right threshold? Should it be per-invariant or per-analyst?
- Should cross-reference verification be transitive? (If A depends on B which depends on C, does A require C to be verified too?)
- How do we handle invariants that are partially met? (e.g. step-verification is implemented for 7/12 steps)
- Should the verification job be a new background agent, a scheduler job, or a CLI skill?
