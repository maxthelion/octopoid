# Test Infrastructure Completion Plan

**Status:** Superseded
**Captured:** 2026-02-20
**Superseded by:** Project `PROJ-f58c4adc` — 18 tasks created with dependency graph. Tasks TASK-test-1-1 through TASK-test-6-2.

## Testing Philosophy

Octopoid is an orchestration system. Its correctness depends on **state transitions**, not business logic — tasks move between queues, agents get spawned and cleaned up, PRs get created and merged, leases expire. When a transition is wrong, the symptom is usually a task stuck in limbo or a silent infinite retry loop. These bugs are invisible until they cascade.

This means:

1. **Integration tests against the real server are the primary safety net.** The local D1 server on port 9787 is cheap to run and enforces real SQL constraints, real queue validation, and real state machine rules. Mocking the server hides the bugs that actually bite us.

2. **Mock the agents, not the infrastructure.** `mock-agent.sh` replaces Claude (expensive, non-deterministic, slow) with a configurable shell script. The fake `gh` replaces GitHub API calls. Everything else — the scheduler, flow engine, steps, SDK, server — runs for real. This catches integration failures that unit tests miss.

3. **Every new flow transition should have an integration test.** If you add a new state, a new condition type, or a new step function, write a test that drives a task through that path using mock agents. The test should verify the task ends up in the right queue with the right metadata.

4. **Unit tests are for pure logic only.** Config parsing, priority sorting, age formatting, YAML validation — these are good unit test targets. Anything that touches the SDK, the filesystem, or subprocess calls should be an integration test instead.

5. **Tests must be deterministic.** No sleeps, no polling loops, no race conditions. Mock agents complete instantly. The fake `gh` returns predictable responses. If a test is flaky, the infrastructure is broken.

## Current State

### What exists

| Layer | Files | Tests | Notes |
|-------|-------|-------|-------|
| Fixture smoke tests | `tests/test_mock_fixtures.py` | 20 | Validates mock-agent.sh, fake gh, git fixtures, run_mock_agent helper |
| Scheduler lifecycle | `tests/integration/test_scheduler_mock.py` | 7 | Happy path, failures, gatekeeper approve/reject, continuation, edge cases |
| Git failure paths | `tests/integration/test_git_failure_scenarios.py` | 6 | Merge conflicts, push failures, no-diff, rebase instructions, combined |
| API server | `tests/integration/test_api_server.py` | 17 | CRUD, orchestrator registration, task filtering |
| Task lifecycle (SDK) | `tests/integration/test_task_lifecycle.py` | 10 | State machine transitions via SDK (no agents) |
| Flow transitions (SDK) | `tests/integration/test_flow.py` | ~15 | Flow engine transitions against server |
| Dependency chains | `tests/integration/test_dependency_chain.py` | ~6 | blocked_by prevents claim, unblocks on completion |
| Hooks | `tests/integration/test_hooks.py` | 14 | Task types, hook resolution, hook execution |
| Unit tests | `tests/test_*.py` | ~200+ | Scheduler guards, pool tracking, flow parsing, config, reports, etc. |

### What's missing

The mock agent infrastructure covers one flow path (default: incoming → claimed → provisional → done) with variations. It doesn't cover:

- Lease expiry and recovery
- Agent pool limits and concurrent claims
- Queue backpressure
- Priority ordering with mock agents
- Step function failures with retry/circuit-breaking
- Multiple agent roles beyond implementer+gatekeeper
- Project/child task flows (not yet implemented, but tests should be ready)

### Infrastructure gaps

- `tests/test_mock_fixtures.py` doesn't run in CI (CI only runs `tests/integration/`)
- No practical guide for writing new mock agent tests
- `tests/integration/README.md` still lists mock agent tests as "TODO" despite them existing
- Fake `gh` is stateless — can't test sequences that depend on prior state

## Plan

### Phase 1: Documentation and CI fixes

**Goal:** Make the existing tests discoverable, documented, and reliably running in CI.

#### Task 1.1: Write testing guide (docs/testing.md)

A practical guide covering:

- **Philosophy** (the 5 principles above, expanded)
- **Test taxonomy**: what goes where (unit vs integration vs mock-agent)
- **How to write a mock agent test** — step-by-step with code template:
  1. Create task via `scoped_sdk.tasks.create()`
  2. Claim via `scoped_sdk.tasks.claim()`
  3. Set up git repo with `_init_git_repo_with_remote()` or `_init_git_repo_basic()`
  4. Run `_run_mock_agent(worktree, task_dir, outcome=..., commits=...)`
  5. Call `handle_agent_result_via_flow()` (or `handle_agent_result()`)
  6. Assert final queue via `scoped_sdk.tasks.get()`
- **How to extend mock-agent.sh** — adding new MOCK_* env vars
- **How to extend fake gh** — adding new subcommands, adding stateful behavior
- **Running tests locally**: server setup, pytest invocations, markers
- **Debugging tips**: reading scheduler logs, checking result.json, common failure modes

#### Task 1.2: Update README and CI

- Update `tests/integration/README.md`: remove stale "TODO" for mock agent tests, add mock test section
- Fix CI (`ci.yml`): run `pytest tests/ -v` instead of just `tests/integration/` so fixture smoke tests and unit tests also run
- Add a separate CI job for unit tests that doesn't need the server

#### Task 1.3: Add testing philosophy to CLAUDE.md

The CLAUDE.md already has a "Testing philosophy: outside-in" section. Expand it to reference `docs/testing.md` and reinforce that agents writing tests should follow the guide.

### Phase 2: Lease and recovery tests

**Goal:** Test that stuck/crashed agents get recovered correctly.

#### Task 2.1: Lease expiry end-to-end test

Use `MOCK_SLEEP` to simulate an agent that takes too long. Verify:
- Task claimed by mock agent
- Agent still running when lease check fires
- Lease expires → task returns to incoming
- Task is re-claimable

This is the most important missing test — lease expiry is the primary recovery mechanism and has had bugs before.

#### Task 2.2: Orphaned agent recovery test

Simulate agent crash (`MOCK_CRASH=true`), then verify `check_and_update_finished_agents` detects the dead PID and moves the task to failed. This partially exists in `test_agent_crash_goes_to_failed` but should be tested through the full scheduler path (PID tracking → detection → cleanup → queue transition).

#### Task 2.3: Double-processing guard test

Verify that processing the same result twice doesn't cause duplicate transitions or errors. Agent finishes → scheduler processes result → result.json still exists → scheduler runs again → should be a no-op.

### Phase 3: Pool and concurrency tests

**Goal:** Test multi-agent scenarios and resource contention.

#### Task 3.1: Pool capacity limits

- Configure a blueprint with `max_instances: 2`
- Create 3 tasks
- Verify only 2 get claimed simultaneously
- Complete one → third gets claimed
- Tests `guard_pool_capacity` end-to-end

#### Task 3.2: Duplicate claim prevention

- Verify `guard_claim_task` dedup: if instance-1 of a blueprint is working on TASK-A, instance-2 doesn't also claim TASK-A
- Uses real pool tracking (running_pids.json)

#### Task 3.3: Priority ordering with mock agents

- Create P0, P1, P2 tasks
- Claim one at a time
- Verify P0 claimed first, then P1, then P2
- Simple but validates a core scheduler guarantee

### Phase 4: Step function failure tests

**Goal:** Test that individual step failures are handled gracefully.

#### Task 4.1: Stateful fake gh

Upgrade `tests/fixtures/bin/gh` to support stateful sequences:
- Track created PRs in a temp file (`GH_STATE_FILE`)
- `gh pr create` writes PR to state; subsequent `gh pr view` returns it
- `gh pr merge` marks it merged; subsequent `gh pr view` shows MERGED
- `gh pr create` on existing branch returns "already exists"
- This unblocks several step failure tests

#### Task 4.2: create_pr failure recovery

Using the stateful fake gh:
- PR already exists → `create_pr` step recovers (tests the fix we made today)
- `gh pr create` returns rate limit error → step fails, task stays claimed for lease recovery
- `gh pr view` transient failure → step retries or fails gracefully

#### Task 4.3: run_tests timeout

- Mock a test runner that takes too long (or use `MOCK_SLEEP` creatively)
- Verify that `run_tests` step timeout results in the task going to failed (not stuck in claimed)
- This was the exact bug that hit e8d479f9 today

#### Task 4.4: merge_pr failure after approval

- Gatekeeper approves, `post_review_comment` succeeds, `merge_pr` fails
- Verify task goes to failed, not done
- PR should still exist (not deleted)

### Phase 5: Flow engine integration tests

**Goal:** Test flow transitions beyond the default flow.

#### Task 5.1: Custom flow with script condition

Create a test flow with a `type: script` condition:
```yaml
"provisional -> done":
  conditions:
    - name: lint_check
      type: script
      script: "exit 0"  # or "exit 1" for failure
      on_fail: incoming
```
Verify script pass → done, script fail → incoming.

#### Task 5.2: Multi-condition ordering

Test a transition with multiple conditions where the first fails:
- Condition 1 (script) fails → task goes to on_fail, condition 2 (agent) never runs
- Validates the "cheap checks first" pattern and short-circuit behavior

#### Task 5.3: Flow with custom queues (extensible queue validation)

Now that the server supports extensible queues:
- Register a flow with custom states (e.g. `testing`, `staging`)
- Verify tasks move through custom queues correctly
- This tests the TASK-26ff1030 work end-to-end

### Phase 6: Backpressure and health tests

**Goal:** Test queue limits and health monitoring.

#### Task 6.1: Backpressure prevents claims

- Configure backpressure limits (e.g. max 3 claimed tasks)
- Create and claim 3 tasks
- Attempt to claim a 4th → should be blocked
- Complete one → 4th becomes claimable

#### Task 6.2: Queue health diagnostics

- Create tasks in various broken states (stuck claimed, expired leases)
- Run `_check_queue_health` (or equivalent)
- Verify health report identifies the problems
- This tests the diagnostic infrastructure from Draft #53

## Task Sizing

| Task | Scope | Estimated turns | Dependencies |
|------|-------|----------------|--------------|
| 1.1 | Write docs/testing.md | 30-40 | None |
| 1.2 | Update README + CI | 20-30 | None |
| 1.3 | Update CLAUDE.md | 10 | 1.1 |
| 2.1 | Lease expiry e2e | 40-60 | None |
| 2.2 | Orphaned agent recovery | 30-40 | None |
| 2.3 | Double-processing guard | 20-30 | None |
| 3.1 | Pool capacity limits | 40-50 | None |
| 3.2 | Duplicate claim prevention | 30-40 | None |
| 3.3 | Priority ordering | 20-30 | None |
| 4.1 | Stateful fake gh | 40-50 | None |
| 4.2 | create_pr failure recovery | 30-40 | 4.1 |
| 4.3 | run_tests timeout | 30-40 | None |
| 4.4 | merge_pr failure after approve | 30-40 | 4.1 |
| 5.1 | Script condition | 30-40 | None |
| 5.2 | Multi-condition ordering | 30-40 | 5.1 |
| 5.3 | Custom queue flows | 40-50 | None |
| 6.1 | Backpressure prevents claims | 30-40 | None |
| 6.2 | Queue health diagnostics | 30-40 | Draft #53 |

## Execution Order

1. **Documentation first** (1.1, 1.2, 1.3) — so agents writing tests follow the right patterns
2. **Lease and recovery** (2.1, 2.2, 2.3) — highest-value gap, has caused real production bugs
3. **Stateful fake gh** (4.1) — unblocks step failure tests
4. **Step failures** (4.2, 4.3, 4.4) — second-highest-value gap, these caused today's infinite loops
5. **Pool and concurrency** (3.1, 3.2, 3.3) — important as we scale to more agents
6. **Flow engine** (5.1, 5.2, 5.3) — validates the flow system we're building towards
7. **Backpressure and health** (6.1, 6.2) — depends on Draft #53 work

Phases 1 and 2 can run in parallel. Within each phase, tasks without dependency arrows can also be parallelized.

## Open Questions

- Should we add a `pytest` marker like `@pytest.mark.mock_agent` to distinguish mock agent tests from SDK-only integration tests? This would let us run mock agent tests separately (they're slower due to subprocess + git setup).
- Should the stateful fake gh use a temp file or an in-memory approach (e.g. a small Python HTTP server)? File-based is simpler but slower; server-based is cleaner but more infrastructure.
- Do we want test coverage reporting? pytest-cov is easy to add but the numbers will be misleading since most of the codebase is scheduler logic that only runs under integration tests.
