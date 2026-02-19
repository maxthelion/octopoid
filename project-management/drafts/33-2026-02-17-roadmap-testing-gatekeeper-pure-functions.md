# Roadmap: Converging Testing, Gatekeeper, and Pure Functions

**Status:** Idea
**Captured:** 2026-02-17
**Related:** Draft 28 (outside-in testing), Draft 29 (fix gatekeeper), Draft 31 (pure functions), Draft 32 (scoped server for testing)

## Raw

> We need to bring a number of threads together. TASK-3ca has failed a number of times. It brings a more flexible agent workflow. The task to add sanity-check gatekeeper has been approved, but is not wired into the flow and scheduler. This is the latest in a long line of silly failures that would have been caught if we had a decent testing strategy. We've built scope into the octopoid server so that we can run end to end tests. I suggest we frame these as flow tests. We should be able to set up a flow, change some of the outcomes (especially in pure function system), and check that we get the right result. Sanity check gatekeeper is important because otherwise we keep checking tasks manually and they often have silly problems with them. This is a waste of time. We need to hook it in, and probably make it a pure function. How do we balance these priorities with a set of tasks that gets us to a better tested system that is less brittle and needs less supervision?

## The Problem

Multiple threads are in flight, each individually justified, but uncoordinated:

1. **TASK-3ca8857a** (agent pool model) — keeps failing. It would give us flexible agent blueprints, but the implementation keeps breaking because there are no tests catching the regressions.

2. **Sanity-check gatekeeper** — approved in concept (draft #29), but not wired into the scheduler or flows. Without it, every task needs manual human review. We keep finding silly problems (missing imports, deleted guards, broken module paths) that a gatekeeper would catch automatically.

3. **Scoped testing** (draft #32, TASK-c8953729) — server now has scope support, SDK needs it. Once done, we can run real end-to-end tests without mocking.

4. **Pure function model** (draft #31) — agents return success/failure, orchestrator handles lifecycle. Simplifies everything but is the biggest change.

These all feed each other: testing needs scope, gatekeeper needs pure-function model (or at least to be wired in), the pool model keeps failing because there's no gatekeeper or tests to catch problems. Meanwhile, every manual review is time we're not spending on fixing the system.

## Proposed Sequencing

The key insight: **test infrastructure first, then use it to build and verify everything else.** Each step creates a foundation the next step relies on.

### Phase 1: Test foundation (unblocks everything)

**TASK-c8953729** — Add scope to Python SDK + scoped test fixture

This is already enqueued. Small, focused, no architectural changes. Once done:
- We can write real tests against a real server
- New features get tested before they ship
- Agents can run scoped tests in their worktrees

### Phase 2: Flow tests as the verification framework

New task: **Write flow tests using scoped SDK**

"Flow tests" exercise the full task lifecycle against a real scoped server:

```python
def test_implement_submit_accept_flow(scoped_sdk):
    """A task goes through incoming → claimed → provisional → done."""
    task = scoped_sdk.tasks.create(id="TEST-001", ...)
    claimed = scoped_sdk.tasks.claim(agent_name="test-impl", ...)
    assert claimed["queue"] == "claimed"

    submitted = scoped_sdk.tasks.submit(task["id"], commits_count=1, turns_used=5)
    assert submitted["queue"] == "provisional"  # state machine transition

    accepted = scoped_sdk.tasks.accept(task["id"], accepted_by="test-gatekeeper")
    assert accepted["queue"] == "done"

def test_reject_returns_to_incoming(scoped_sdk):
    """A rejected task goes back to incoming."""
    # ... create, claim, submit ...
    rejected = scoped_sdk.tasks.reject(task["id"], reason="tests fail")
    assert rejected["queue"] == "incoming"
```

These are deterministic, fast, and test the real server state machine. They would have caught:
- The `guard_claim_task` deletion (claim would fail)
- The branch NOT NULL constraint (create would fail)
- The flow column migration issue (create would 500)

In the pure function model, flow tests become even more powerful — you can test "if the gatekeeper returns reject, does the task go back to incoming?" without spawning Claude.

### Phase 3: Wire in gatekeeper as pure function

New task: **Implement gatekeeper as pure-function agent** (from draft #29 revised)

With flow tests in place, we can:
1. Write the gatekeeper test first: "given a provisional task, gatekeeper claims it, returns approve → task moves to done"
2. Implement the gatekeeper to pass the test
3. Wire it into the scheduler
4. Delete the broken Python role module

The gatekeeper is the simplest pure-function agent (read diff, return approve/reject). It proves the pattern before we apply it to implementers.

This directly solves the "manual review waste" problem — every provisional task gets automated review.

### Phase 4: Harden the pool model

TASK-3ca8857a (or a replacement) implements the agent pool model, but now:
- Flow tests verify the claim/spawn/submit pipeline works
- The gatekeeper catches implementation bugs before merge
- We have confidence the changes don't break existing flows

### Phase 5: Pure-function implementer (stretch)

Apply the pure-function model to the implementer. With the gatekeeper pattern proven and flow tests in place, this becomes a well-tested refactor rather than a leap of faith.

## Why this order

| Phase | What it gives us | What it unblocks |
|-------|-----------------|-----------------|
| 1. SDK scope | Real tests possible | Everything |
| 2. Flow tests | Catch regressions automatically | Safe to make changes |
| 3. Gatekeeper | Automated review, less manual work | Agents can ship without human bottleneck |
| 4. Pool model | Flexible agent config | Multiple agents per role |
| 5. Pure implementer | Clean architecture | Reliable agent lifecycle |

The temptation is to jump to phase 3 (gatekeeper) or 4 (pool model) because they're the most visible improvements. But without phases 1-2, we keep shipping broken changes and discovering them through manual review — the exact problem we're trying to solve.

## What "flow tests" means concretely

Flow tests are not full integration tests (they don't spawn Claude). They test the **orchestrator's lifecycle logic** against a **real server**:

- **State machine transitions**: incoming→claimed→provisional→done, with all the guards and side effects
- **Claim filtering**: role_filter, type_filter, scope isolation
- **Error paths**: reject, requeue, lease expiry
- **Pure function pipelines**: "if agent returns X, orchestrator does Y"

In the pure-function model, the agent's output is a JSON blob. Flow tests can provide fake agent results and verify the orchestrator handles them correctly:

```python
def test_gatekeeper_reject_flow(scoped_sdk):
    """Orchestrator correctly processes a gatekeeper rejection."""
    # Setup: task in provisional
    task = create_provisional_task(scoped_sdk)

    # Simulate gatekeeper result
    result = {"status": "success", "decision": "reject", "comment": "Tests fail"}

    # Run the orchestrator's post-gatekeeper pipeline
    handle_gatekeeper_result(task, result, sdk=scoped_sdk)

    # Verify: task back in incoming with feedback
    updated = scoped_sdk.tasks.get(task["id"])
    assert updated["queue"] == "incoming"
```

This is the sweet spot: testing real orchestrator logic with real server state, without needing Claude.

## Open Questions

- Should we pause TASK-3ca8857a (pool model) until phases 1-2 are done, or let it keep trying in parallel? It's burning agent cycles but might eventually land.
- How many flow tests do we need before we're confident enough to wire in the gatekeeper? A handful covering the happy path + reject path, or comprehensive coverage?
- Should flow tests live in `tests/integration/` (alongside existing API tests) or a new `tests/flows/` directory?

## Possible Next Steps

- Process this into a sequenced project with blocked_by chains
- Fix TASK-c8953729 task file (review found issues with scope injection, mock conflict, test conversion underspecification)
- Pause TASK-3ca8857a until test infrastructure is in place
