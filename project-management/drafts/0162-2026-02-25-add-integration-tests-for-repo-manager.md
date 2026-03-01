# Add integration tests for repo_manager: rebase_on_base with real git conflict using conflicting_repo fixture

**Author:** testing-analyst
**Captured:** 2026-02-25

## Gap

`orchestrator/repo_manager.py` has 6 unit test files, but every test mocks `_run_git`
via `patch.object(repo, "_run_git")`. No test exercises real git operations. The
`rebase_on_base()` method is especially risky: it's auto-injected as a terminal step for
every task completion (`_inject_terminal_steps()` in `flow.py`), meaning a silent failure
here breaks the entire task pipeline. Yet all its edge cases (conflict detection, abort
behavior, up-to-date detection) are only verified against mocked subprocess returns.

## Proposed Test

Add a new test file `tests/test_repo_manager_integration.py` that uses the existing
`conflicting_repo` and `test_repo` fixtures (from `tests/fixtures/conftest_mock.py`) to
test `RepoManager.rebase_on_base()` against a real git repository:

**Scenario 1 — Already up to date:**
- Use `test_repo` fixture (task branch is on base with no divergence)
- Call `repo.rebase_on_base(base_branch="main")`
- Assert `result.status == RebaseStatus.UP_TO_DATE`

**Scenario 2 — Successful rebase:**
- Use `test_repo`, create a task branch, add a commit to base, push base
- Call `repo.rebase_on_base()`
- Assert `result.status == RebaseStatus.SUCCESS`
- Assert the task branch's HEAD is now ahead of base (actually rebased)

**Scenario 3 — Real conflict, abort leaves clean state:**
- Use `conflicting_repo` fixture (task branch and base have diverging changes to same file)
- Checkout task-branch in the working clone
- Call `repo.rebase_on_base(base_branch=<base_branch>)`
- Assert `result.status == RebaseStatus.CONFLICT`
- Assert the repo is NOT in a rebase-in-progress state (i.e. `git rebase --abort` ran)
  by checking `.git/rebase-merge` doesn't exist

No mocking — all scenarios use real subprocess git calls on temporary repos.

## Why This Matters

`rebase_on_base()` is auto-injected into every task completion flow. A bug that
causes it to:
- misdetect "up to date" (skipping a needed rebase),
- fail to abort after a conflict (leaving the worktree in `rebase-in-progress` state), or
- report SUCCESS on a silent git failure

...would break every task completion silently. The unit tests use mocked
`subprocess.CompletedProcess` objects and cannot catch any of these real-world failure
modes. The `conflicting_repo` fixture already exists specifically for this kind of test
and is underused.


## Invariants

No new invariants — this proposes adding integration tests for `repo_manager.py` with real git operations. Testing improvement only.
