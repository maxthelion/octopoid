# Add integration tests for steps.py: PushBranchStep idempotency (pre_check skips already-pushed branch)

**Author:** testing-analyst
**Captured:** 2026-02-28

## Gap

`octopoid/steps.py` was recently refactored (commit `197bdb8`) to introduce a
three-phase Step abstraction (`pre_check`, `execute`, `verify`) to fix production
failure classes: ghost completions and double-execution bugs. The refactor added 50
new unit tests in `tests/test_step_verification.py`, but **all use MagicMock Steps**.

The actual Step class implementations — `PushBranchStep`, `CreatePRStep`, `MergePRStep`,
`RebaseOnBaseStep` — have **no integration tests that exercise their three-phase
behaviour** with real git or real server operations. The unit tests verify the runner
machinery works but cannot catch regressions in the idempotency logic of the real
classes.

## Proposed Test

Add integration tests in `tests/integration/test_steps_idempotency.py` using the
`test_repo` fixture (a local bare git remote) and `scoped_sdk` (real server on port 9787).

**Scenario 1 — PushBranchStep.check_done() skips re-push:**
1. Create a task on the real server (scoped_sdk)
2. Set up a git worktree with `test_repo` fixture (has local bare remote)
3. Commit a change, call `PushBranchStep().execute(ctx)` — verifies branch is pushed
4. Call `PushBranchStep().check_done(ctx)` again — must return `True` (already done)
5. Assert `execute()` is NOT called a second time by `execute_steps()`

**Scenario 2 — RebaseOnBaseStep.check_done() skips no-op rebase:**
1. Set up git worktree already rebased on base (no divergence)
2. Call `RebaseOnBaseStep().check_done(ctx)` — must return `True`
3. Assert the rebase subprocess is not invoked

Both scenarios use only local git operations (no GitHub API calls) so they run
reliably in CI.

## Why This Matters

The three-phase Step abstraction was introduced specifically to fix:
1. **Ghost completions**: a step that previously succeeded but failed to record
   its result would re-run on the next tick — potentially merging the same PR twice
2. **Double push**: re-pushing an already-pushed branch causes `Everything up-to-date`
   but also creates races if the branch was deleted

Without integration tests, any regression in `check_done()` logic for the actual
Step classes would go undetected. The mocked unit tests verify the runner behaviour
but cannot test whether `PushBranchStep.check_done()` correctly reads remote git
state. This code path runs on every successful task completion and a regression
would silently cause tasks to re-execute completed work or fail with confusing errors.
