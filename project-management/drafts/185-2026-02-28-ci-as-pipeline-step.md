---
**Processed:** 2026-02-28
**Mode:** human-guided
**Actions taken:**
- Discovered `check_ci` step already exists in `steps.py:83` and is in `default.yaml` flow
- But it runs AFTER gatekeeper approval (in `provisional → done` runs), not before
- CI failure goes through intervention/fixer, not back to implementer
- Updated with async checks design
- Scoped to simplest robust version: built-in checks only, parallel only, one check (check_ci)
**Outstanding items:** Ready for enqueue
---

# CI as a pipeline step before gatekeeper

**Captured:** 2026-02-28

## Raw

> My preferred approach would be that CI is part of the pipeline but not duplicated in gatekeeper. What's a good way for that to fit in? before or after gatekeeper? What happens if CI fails? Thrown back for implementers to fix programmatically? LLM gatekeeper shouldn't need to be involved
>
> we want check_ci to be before the gatekeeper. The main issue seems to be that this needs to be run asynchronously. Basically, the process should be that the implementer stops work. Scheduler sees they are finished and sets of a bunch of checks on the work. The checks write their results somewhere, and the scheduler notes that they've been started. The scheduler looks for completed checks on every tick and notes whether or not all the conditions are met. If not, we can specify what happens (eg an onfail event).

## Problem

CI runs after gatekeeper approval. PR #261 was approved by the gatekeeper (811/811 local tests passed) but broke 6 integration tests that only CI runs. The gatekeeper can't catch these — it doesn't have a test server.

## Design

### Checks: pure functions polled by the scheduler

A check is a pure function: `(task) → pass | fail | pending`. Stateless, idempotent. The scheduler calls it on every tick for tasks waiting at a transition.

```python
class CheckResult(Enum):
    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending"

@register_check("check_ci")
def check_ci(task: dict) -> CheckResult:
    pr_number = task.get("pr_number")
    if not pr_number:
        return CheckResult.PASS
    # poll gh pr checks, return PASS/FAIL/PENDING
```

### Flow configuration

```yaml
provisional -> done:
  checks: [check_ci]
  on_checks_fail: incoming

  condition:
    type: agent
    agent: gatekeeper
    on_fail: incoming
  runs: [rebase_on_base, merge_pr, update_changelog]
```

`checks` run before `condition`. The gatekeeper can only claim the task once all checks pass.

### Scheduler lifecycle

On each tick, for each task waiting at a transition with `checks`:
1. Call all check functions (parallel — all at once)
2. All return PASS → task is eligible for the transition's condition (gatekeeper can claim)
3. Any return FAIL → requeue to `on_checks_fail` queue with failure context
4. Any return PENDING → skip, try next tick

### What happens on CI failure

Task goes back to `incoming` with CI failure context. Implementer picks it up, fixes code, pushes. Fully programmatic — no gatekeeper or fixer involved.

### Scope (v1)

Keep it simple and robust:
- **Built-in checks only** — no user-extensible scripts, just registered Python functions
- **Parallel only** — all checks fire at once, no chaining
- **One check to start** — `check_ci` (already mostly implemented in `steps.py:83`)
- **Results as messages** — post check results as task messages for dashboard visibility

### What changes

1. Add `CheckResult` enum and `@register_check` decorator to a new `checks.py` module
2. Move `check_ci` logic from `steps.py` step to `checks.py` check (different signature — takes `task`, returns `CheckResult` instead of raising)
3. Add `checks` and `on_checks_fail` to flow schema
4. Update scheduler to evaluate checks before allowing condition (gatekeeper claim)
5. Remove `check_ci` from `runs` list in `default.yaml`
6. Remove `run-tests` from gatekeeper scripts (CI already covers it)

### Testing

Pure functions with a registry make this easy to test:

```python
@register_check("test_pass")
def test_pass(task: dict) -> CheckResult:
    return CheckResult.PASS

@register_check("test_fail")
def test_fail(task: dict) -> CheckResult:
    return CheckResult.FAIL
```

Integration tests with `scoped_sdk`:
- All checks pass → gatekeeper can claim
- Check fails → task bounced to incoming with context
- Check pending → task stays, not claimable
- No checks configured → gatekeeper can claim immediately (backwards compatible)

## Invariants

- **ci-before-gatekeeper**: The gatekeeper never reviews a task whose CI has not passed. CI is a prerequisite for gatekeeper review, not a parallel check.
- **ci-failure-is-programmatic**: When CI fails, the task is returned to the implementer without LLM gatekeeper involvement. The failure context (which tests failed, output) is included in the requeued task.

## Context

Task 795d194c was approved by the gatekeeper with 811/811 tests passing, but broke 6 integration tests in CI. The `check_ci` step already exists in `steps.py:83` but runs after gatekeeper approval in the `provisional → done` runs list. Moving it before the gatekeeper requires the async checks concept because CI takes minutes to complete and can't block a synchronous step list.
