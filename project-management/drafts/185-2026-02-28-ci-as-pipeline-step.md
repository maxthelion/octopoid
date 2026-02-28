# CI as a pipeline step before gatekeeper

**Captured:** 2026-02-28

## Raw

> My preferred approach would be that CI is part of the pipeline but not duplicated in gatekeeper. What's a good way for that to fit in? before or after gatekeeper? What happens if CI fails? Thrown back for implementers to fix programmatically? LLM gatekeeper shouldn't need to be involved

## Idea

CI (GitHub Actions) should be a step in the task flow, not something the gatekeeper duplicates. Currently:

- **Gatekeeper** runs `pytest` locally in its worktree (811 unit tests)
- **CI** runs both unit tests and integration tests (with a real server)
- The gatekeeper can't run integration tests (no server), so it approves code that breaks them
- This just happened: PR #261 passed gatekeeper review (811/811 tests) but broke 6 integration tests in CI

The fix: CI runs **before** the gatekeeper, as a flow step. If CI fails, the task goes back to the implementer to fix — no LLM involved, just a programmatic bounce. The gatekeeper only sees tasks where CI already passes.

### Flow change

Current:
```
claimed → provisional: [rebase_on_base, push_branch, create_pr]
provisional → done: gatekeeper reviews (runs tests locally, duplicating CI)
```

Proposed:
```
claimed → provisional: [rebase_on_base, push_branch, create_pr]
provisional: check_ci step polls GitHub Actions status
  - CI passes → gatekeeper can claim
  - CI fails → task bounces back to implementer (requeue to incoming with CI failure context)
provisional → done: gatekeeper reviews (no need to run tests — CI already passed)
```

The `check_ci` step is an async polling step — it waits for GitHub Actions to report a result on the PR, then either proceeds or bounces. The gatekeeper's `run-tests` script becomes unnecessary (or optional as a sanity check).

### What happens on CI failure

The task is requeued to `incoming` with context about which tests failed. The implementer picks it up, sees the failure output, fixes the code, and pushes again. This is fully programmatic — no LLM gatekeeper needed to diagnose "tests are failing". The implementer agent is the right one to fix code.

## Invariants

- **ci-before-gatekeeper**: The gatekeeper never reviews a task whose CI has not passed. CI is a prerequisite for gatekeeper review, not a parallel check.
- **ci-failure-is-programmatic**: When CI fails, the task is returned to the implementer without LLM gatekeeper involvement. The failure context (which tests failed, output) is included in the requeued task.

## Context

Task 795d194c (intervention-first failure routing) was approved by the gatekeeper with 811/811 tests passing, but broke 6 integration tests in CI. The gatekeeper couldn't catch this because it doesn't run integration tests — it has no test server. Rather than giving the gatekeeper a test server (duplicating CI infrastructure), CI should be a gate in the pipeline that runs before the gatekeeper.

Also note: some dangling commits in the repo suggest a `check_ci` step was attempted before but never landed.

## Open Questions

- How does `check_ci` handle the async nature of CI? Poll every N seconds? Webhook? Or just check on the next scheduler tick?
- What's the timeout? If CI hasn't reported after X minutes, what happens?
- Should `check_ci` block the task in `provisional` (preventing gatekeeper claim) or use a separate queue state?
- Should the gatekeeper still run a fast local test suite as a sanity check, or rely entirely on CI?

## Possible Next Steps

- Implement `check_ci` step that polls GitHub Actions status via `gh` CLI
- Add `check_ci` to the `claimed → provisional` flow (after `create_pr`)
- Add CI failure → requeue logic with failure context
- Remove or simplify gatekeeper's `run-tests` script
- Update flow definition in `default.yaml`
