# Mock Claude Agents in Integration Tests for Determinism and Cost

**Status:** Idea
**Captured:** 2026-02-18
**Related:** Draft 28 (outside-in testing), Draft 32 (scoped local server), Draft 31 (agents as pure functions)

## Raw

> Claude based agents should be mocked in integration tests so that we aren't burning credits and are acting deterministically
>
> There are some points about git being mocked too. We need to test flows with various combinations of things (like merge conflicts) that are difficult to set up state for.

## Idea

Integration tests should exercise the full scheduler pipeline (claim → spawn → result → flow steps → transition) without calling the Claude API or GitHub API. Mock the expensive/non-deterministic boundaries, keep everything else real.

### What's real, what's mocked

| Layer | Real or Mock | Why |
|-------|-------------|-----|
| **Server** | Real (local:9787, scoped) | Already done. Tests real API validation, state machine |
| **SDK** | Real | Tests real request/response shapes |
| **Scheduler logic** | Real | Guards, flows, transitions, backpressure — this is what we're testing |
| **Flow dispatch** | Real | The YAML-driven state machine is the core thing to validate |
| **Agent (Claude)** | **Mock** | Expensive, non-deterministic, slow. Replace with a script that writes canned `result.json` |
| **Git (local)** | Real | Cheap, deterministic. Use real repos with pre-arranged states |
| **GitHub API** | **Mock** | `gh pr create`, `gh pr merge`, merge status checks. Replace with a fake that returns controlled responses |

### Mock agent: one script, configured via environment

Don't patch `spawn_agent` in Python — that skips too much of the real machinery. Instead, use a single mock agent shell script that reads its behavior from environment variables. The scheduler already writes `env.sh` for every agent; tests add mock-specific vars.

**Environment variables:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `MOCK_OUTCOME` | `success` | Result outcome: `success`, `failure`, `needs_continuation` |
| `MOCK_DECISION` | _(empty)_ | For gatekeepers: `approve`, `reject`. Empty for implementers |
| `MOCK_COMMENT` | _(empty)_ | Rejection/approval comment text |
| `MOCK_REASON` | _(empty)_ | Failure reason text |
| `MOCK_COMMITS` | `1` | Number of git commits to make. `0` for no-commit scenarios |
| `MOCK_CRASH` | `false` | If `true`, exit non-zero without writing result.json |
| `MOCK_SLEEP` | `0` | Seconds to sleep before acting (test lease timeout) |

**The script:**

```bash
#!/bin/bash
# tests/fixtures/mock-agent.sh — one script, any behavior

cd "$TASK_WORKTREE"

# Simulate crash (no result.json)
if [ "$MOCK_CRASH" = "true" ]; then exit 1; fi

# Simulate slowness (lease timeout testing)
if [ "${MOCK_SLEEP:-0}" -gt 0 ] 2>/dev/null; then sleep "$MOCK_SLEEP"; fi

# Make commits
for i in $(seq 1 "${MOCK_COMMITS:-1}"); do
  echo "change $i at $(date +%s)" >> mock-changes.txt
  git add mock-changes.txt
  git commit -m "mock commit $i"
done

# Build result.json from env vars
RESULT="{\"outcome\": \"${MOCK_OUTCOME:-success}\""
[ -n "$MOCK_DECISION" ] && RESULT="$RESULT, \"decision\": \"$MOCK_DECISION\""
[ -n "$MOCK_COMMENT" ] && RESULT="$RESULT, \"comment\": \"$MOCK_COMMENT\""
[ -n "$MOCK_REASON" ] && RESULT="$RESULT, \"reason\": \"$MOCK_REASON\""
RESULT="$RESULT}"

echo "$RESULT" > "$TASK_DIR/result.json"
```

The scheduler spawns this script the same way it spawns a real agent — `claim_and_prepare_task`, worktree setup, env.sh, everything. The only thing that's different is the agent binary. This means the test exercises:
- Guard chain evaluation
- Task claiming and lease management
- Worktree creation and detached HEAD
- `check_and_update_finished_agents` detecting completion
- `handle_agent_result` reading the result
- Flow step execution (push_branch, create_pr, etc.)

**Test configuration examples:**

```python
# Happy implementer: 2 commits, success
env = {"MOCK_OUTCOME": "success", "MOCK_COMMITS": "2"}

# Gatekeeper approves
env = {"MOCK_OUTCOME": "success", "MOCK_DECISION": "approve", "MOCK_COMMITS": "0"}

# Gatekeeper rejects with feedback
env = {"MOCK_OUTCOME": "success", "MOCK_DECISION": "reject",
       "MOCK_COMMENT": "Tests fail on line 42", "MOCK_COMMITS": "0"}

# Agent fails
env = {"MOCK_OUTCOME": "failure", "MOCK_REASON": "Could not parse requirements"}

# Agent crashes (no result.json written)
env = {"MOCK_CRASH": "true"}

# Agent hangs (lease timeout test)
env = {"MOCK_SLEEP": "300", "MOCK_OUTCOME": "success"}

# Agent succeeds but makes no commits (edge case for push_branch)
env = {"MOCK_OUTCOME": "success", "MOCK_COMMITS": "0"}
```

### Mock GitHub API: controlled git remote

The flow steps that touch GitHub are:
- `push_branch` → `git push origin HEAD` (in `repo_manager.py`)
- `create_pr` → calls `RepoManager.create_pr` which runs `gh pr create`
- `merge_pr` → calls `approve_and_merge` which runs `gh pr merge`
- Merge status checks → `gh pr view --json mergeStateStatus`

Two approaches:

**Option A: Local bare repo as remote.** Set up a bare git repo as `origin` instead of GitHub. `git push` works natively. Mock only the `gh` commands:

```python
@pytest.fixture
def test_repo(tmp_path):
    """Create a local git repo with a bare remote — no GitHub needed."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True)

    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(bare), str(work)], check=True)

    # Seed with an initial commit on the base branch
    (work / "README.md").write_text("# Test repo")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=work, check=True)
    subprocess.run(["git", "push"], cwd=work, check=True)

    return {"bare": bare, "work": work}
```

**Option B: Mock `gh` CLI.** Put a fake `gh` script on PATH that returns controlled responses:

```bash
#!/bin/bash
# tests/fixtures/bin/gh — fake GitHub CLI
case "$1 $2" in
  "pr create")
    echo "https://github.com/test/repo/pull/99"
    ;;
  "pr merge")
    echo "✓ Merged"
    ;;
  "pr view")
    echo '{"mergeStateStatus":"CLEAN","number":99,"url":"https://github.com/test/repo/pull/99"}'
    ;;
esac
```

Option A is better for testing push/merge mechanics. Option B is simpler and enough if we only care about flow transitions.

**Recommended: Option A for git, Option B for `gh`.** Push to a real local bare repo (tests that `push_branch` actually works with real git), but mock `gh` (don't need a real GitHub PR to test flow transitions).

### Testing merge conflicts

This is where real git repos pay off. Set up conflicting states in fixtures:

```python
@pytest.fixture
def conflicting_repo(test_repo):
    """Set up a repo where the task branch conflicts with the base branch."""
    work = test_repo["work"]
    bare = test_repo["bare"]

    # Create a task branch with a change
    subprocess.run(["git", "checkout", "-b", "agent/TASK-test"], cwd=work, check=True)
    (work / "shared-file.txt").write_text("agent's version")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-m", "agent change"], cwd=work, check=True)
    subprocess.run(["git", "push", "-u", "origin", "agent/TASK-test"], cwd=work, check=True)

    # Back on base branch, make a conflicting change
    subprocess.run(["git", "checkout", "main"], cwd=work, check=True)
    (work / "shared-file.txt").write_text("base branch version")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-m", "conflicting base change"], cwd=work, check=True)
    subprocess.run(["git", "push"], cwd=work, check=True)

    return test_repo
```

Now the fake `gh pr view` returns `{"mergeStateStatus": "CONFLICTING"}` and the test can verify that the scheduler correctly handles the conflict — rejecting the task, adding `needs_rebase`, routing back to incoming, etc.

### Configuring the `gh` mock per-test

The fake `gh` also needs to be configurable per-test — a merge conflict test needs `gh pr view` to return `CONFLICTING`, while the happy path needs `CLEAN`. Same approach: env vars.

```bash
#!/bin/bash
# tests/fixtures/bin/gh — fake GitHub CLI, configured via env

case "$1 $2" in
  "pr create")
    echo "https://github.com/test/repo/pull/${GH_MOCK_PR_NUMBER:-99}"
    ;;
  "pr merge")
    if [ "$GH_MOCK_MERGE_FAIL" = "true" ]; then
      echo "! Pull request merge failed" >&2
      exit 1
    fi
    echo "✓ Merged"
    ;;
  "pr view")
    STATUS="${GH_MOCK_MERGE_STATUS:-CLEAN}"
    echo "{\"mergeStateStatus\":\"$STATUS\",\"number\":${GH_MOCK_PR_NUMBER:-99},\"url\":\"https://github.com/test/repo/pull/${GH_MOCK_PR_NUMBER:-99}\"}"
    ;;
esac
```

Test sets:
```python
# Happy path: PR creates and merges cleanly
env = {"GH_MOCK_MERGE_STATUS": "CLEAN", "GH_MOCK_PR_NUMBER": "42"}

# Merge conflict: PR exists but can't merge
env = {"GH_MOCK_MERGE_STATUS": "CONFLICTING", "GH_MOCK_PR_NUMBER": "42"}

# Merge fails at merge time (e.g. branch protection)
env = {"GH_MOCK_MERGE_FAIL": "true"}
```

### Scenario matrix

All scenarios use the same `mock-agent.sh` and `gh` mock, configured differently:

| Scenario | Agent env | Git state | `gh` env | Verifies |
|----------|----------|-----------|----------|----------|
| Happy path: implement + approve | `OUTCOME=success` → `DECISION=approve` | Clean | `STATUS=CLEAN` | Full lifecycle incoming→claimed→provisional→done |
| Agent fails | `OUTCOME=failure` | — | — | claimed→failed transition |
| Agent crashes (no result) | `CRASH=true` | — | — | Orphan detection, claimed→incoming |
| Gatekeeper rejects | `OUTCOME=success` → `DECISION=reject` | Clean | — | provisional→incoming with feedback |
| PR has merge conflicts | `OUTCOME=success` | Conflicting | `STATUS=CONFLICTING` | Conflict detection, needs_rebase flow |
| Merge fails at merge time | `OUTCOME=success` → `DECISION=approve` | Clean | `MERGE_FAIL=true` | merge_pr step error → task not accepted (Draft 45 bug) |
| Push fails | `OUTCOME=success` | Bare remote gone | — | push_branch error handling |
| Tests fail in CI | `OUTCOME=success` | Clean, failing tests | — | run_tests step raises, task goes to failed |
| No commits | `OUTCOME=success, COMMITS=0` | No diff | — | push_branch with nothing to push |
| Lease timeout | `SLEEP=300` | — | — | Lease expiry requeues task |
| Needs continuation | `OUTCOME=needs_continuation` | — | — | claimed→needs_continuation transition |
| Rejection + conflict | `OUTCOME=success` → `DECISION=reject` | Conflicting | `STATUS=CONFLICTING` | Rejection + conflict = needs_rebase + incoming |
| Multiple rejections | `OUTCOME=success` → `DECISION=reject` x3 | Clean | — | rejection_count increments, task keeps cycling |

### How tests orchestrate the pipeline

A test doesn't call the scheduler's main loop — it calls the individual functions in sequence, with controlled inputs. A `run_mock_agent` helper wraps the env setup:

```python
def run_mock_agent(task_dir: Path, agent_env: dict | None = None, gh_env: dict | None = None):
    """Run the mock agent script with configured behavior."""
    env = os.environ.copy()
    env["TASK_DIR"] = str(task_dir)
    env["TASK_WORKTREE"] = str(task_dir / "worktree")

    # Agent behavior
    for k, v in (agent_env or {}).items():
        env[f"MOCK_{k.upper()}"] = str(v)

    # gh mock behavior
    for k, v in (gh_env or {}).items():
        env[f"GH_MOCK_{k.upper()}"] = str(v)

    # Prepend fake gh to PATH
    env["PATH"] = f"tests/fixtures/bin:{env['PATH']}"

    subprocess.run(
        ["bash", "tests/fixtures/mock-agent.sh"],
        env=env, cwd=task_dir / "worktree",
    )
```

**Happy path test:**

```python
def test_happy_path_lifecycle(scoped_sdk, test_repo):
    """Full lifecycle: incoming → claimed → provisional → done."""
    # 1. Create task on scoped server
    scoped_sdk.tasks.create(id="TASK-test-001", title="Test task", queue="incoming", ...)

    # 2. Claim
    claimed = scoped_sdk.tasks.claim(agent_name="test-agent", ...)
    assert claimed["id"] == "TASK-test-001"

    # 3. Prepare task directory
    task_dir = prepare_task_directory("TASK-test-001")

    # 4. Run mock implementer: 2 commits, success
    run_mock_agent(task_dir,
        agent_env={"outcome": "success", "commits": "2"},
        gh_env={"merge_status": "CLEAN", "pr_number": "42"})

    # 5. Handle result (real function under test)
    handle_agent_result("TASK-test-001", "test-agent", task_dir)

    # 6. Verify: claimed → provisional
    task = scoped_sdk.tasks.get("TASK-test-001")
    assert task["queue"] == "provisional"
    assert task["pr_number"] == 42

    # 7. Run mock gatekeeper: approve
    run_mock_agent(task_dir,
        agent_env={"outcome": "success", "decision": "approve", "commits": "0"})
    handle_agent_result("TASK-test-001", "test-gatekeeper", task_dir)

    # 8. Verify: provisional → done
    task = scoped_sdk.tasks.get("TASK-test-001")
    assert task["queue"] == "done"
```

**Merge conflict test:**

```python
def test_merge_conflict_blocks_acceptance(scoped_sdk, conflicting_repo):
    """PR with merge conflicts should not be accepted."""
    # ... setup task in provisional with a PR ...

    # Gatekeeper approves, but gh reports CONFLICTING
    run_mock_agent(task_dir,
        agent_env={"outcome": "success", "decision": "approve", "commits": "0"},
        gh_env={"merge_status": "CONFLICTING", "merge_fail": "true"})
    handle_agent_result("TASK-test-001", "test-gatekeeper", task_dir)

    # Task should NOT advance to done — merge_pr fails
    task = scoped_sdk.tasks.get("TASK-test-001")
    assert task["queue"] != "done"
    assert task.get("needs_rebase") == 1
```

**Crash/orphan test:**

```python
def test_agent_crash_requeues_task(scoped_sdk, test_repo):
    """Agent crash (no result.json) should requeue to incoming, not orphan."""
    # ... setup task in claimed ...

    run_mock_agent(task_dir, agent_env={"crash": "true"})
    handle_agent_result("TASK-test-001", "test-agent", task_dir)

    task = scoped_sdk.tasks.get("TASK-test-001")
    assert task["queue"] == "incoming"  # NOT stuck in claimed
```

Every step is real code except the agent process and `gh` CLI. When `handle_agent_result` is broken (the bugs we keep finding), these tests catch it.

## What this gives us

- **Reproduce every production bug as a test.** Task stuck in claimed? Write a test with `crash.sh` agent and verify the orphan sweep. `can_transition` accepting on failure? Write a test where `gh pr merge` fails and verify the task doesn't advance.
- **Test flow YAML changes safely.** Change the flow, run the matrix, see what breaks.
- **Fast.** No Claude API calls, no GitHub API calls. Each test is local git + local server + shell scripts. Should run in seconds.
- **Deterministic.** Same inputs, same outputs, every time.

## Open Questions

- Should `prepare_task_directory` be a real scheduler function (used in prod and test) or a test-only helper? If real, it's a refactor of the scheduler's spawn logic to be more testable.
- How do we handle the `run_tests` step? It shells out to pytest/npm in the worktree. For tests, the mock repo won't have real tests. Options: skip the step in test flows, or seed the fixture repo with a minimal passing test.
- Should parameterized tests be generated from the scenario matrix, or hand-written? Parameterized is more maintainable as the matrix grows, but harder to debug individual failures.
- Should the `gh` mock log what it was called with, so tests can assert "create_pr was called with the right title" etc.?

## Possible Next Steps

1. Create `tests/fixtures/mock-agent.sh` (single configurable script)
2. Create `tests/fixtures/bin/gh` (configurable fake GitHub CLI)
3. Build the `test_repo` and `conflicting_repo` pytest fixtures
4. Write `run_mock_agent` helper and the happy-path lifecycle test as proof of concept
5. Expand to the full scenario matrix
6. Integrate with draft 44's unified result handler (test it before it ships)
