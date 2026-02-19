# Scheduler Integration Tests: End-to-End Spawn Pipeline

**Status:** Idea
**Captured:** 2026-02-17

## Raw

> Integration tests have been failing us recently. They aren't catching regressions at all. Commit d858559 deleted guard_claim_task and completely broke implementer spawning — 50+ scheduler ticks with zero successful claims and nothing caught it.

## Idea

The existing test suite has a critical gap: nothing tests the scheduler's end-to-end flow from incoming task through to agent spawn. Unit tests mock everything in isolation, integration tests only cover the server API. The path that actually breaks (guard chain → claim → spawn strategy → process start) is completely untested.

### What's tested today

- **Unit tests** (`test_scheduler_refactor.py`): Test individual guards with mocked state. When d858559 deleted `guard_claim_task` from `AGENT_GUARDS`, the tests passed because they test whatever's in the list — they don't assert which guards should be present.
- **Integration tests** (`tests/integration/`): Test server API endpoints (task CRUD, lifecycle transitions). They never touch the scheduler.
- **No test** exercises: incoming task → guard chain passes → task claimed on server → correct spawn strategy selected → agent process starts.

### What broke undetected

1. `guard_claim_task()` deleted — implementers never claim tasks
2. `ctx.claimed_task` always None — `spawn_implementer` path unreachable
3. Implementers routed to `spawn_worktree` → `spawn_agent()` → `python -m orchestrator.roles.implementer` → crash
4. 50+ consecutive failures per implementer, visible only in state.json

### What should be tested

**Level 1: Guard chain composition** (unit test)
- Assert `AGENT_GUARDS` contains the expected guards in order
- Assert `guard_claim_task` is in the list
- Regression: removing a guard from the list should fail a test

**Level 2: Spawn strategy selection** (unit test)
- Given `spawn_mode=scripts` and `claimed_task` is set → returns `spawn_implementer`
- Given `spawn_mode=scripts` and `claimed_task` is None → does NOT return `spawn_implementer`
- Given `lightweight=True` → returns `spawn_lightweight`

**Level 3: Scheduler pipeline** (integration test, requires running server)
- Create a task via API → run one scheduler tick → assert task is claimed → assert agent process started
- This is the critical path test. It needs a real server (or mock) and real scheduler code, not mocked guards.

**Level 4: Agent crash detection** (unit test)
- `check_and_update_finished_agents()` detects dead PIDs and resets state
- Orphaned agents (running=True, dead PID) get cleaned up
- Consecutive failure counter increments correctly

## Context

Discovered during investigation of why TASK-3ca8857a sat in incoming for hours. The d858559 commit ("delete old code paths replaced by flows") claimed flows replaced `guard_claim_task`, but they don't — nothing else claims tasks. The commit passed all existing tests.

## Open Questions

- Should scheduler integration tests run against a real server (like the existing API integration tests) or use mocks?
- How to test agent spawn without actually spawning a Claude process? Mock `invoke_claude()` / `spawn_agent()` but keep everything else real?
- Should there be a CI check that runs the scheduler once against a test server?

## Possible Next Steps

- Add Level 1 + 2 tests immediately (unit tests, no infrastructure needed)
- Design Level 3 test harness (scheduler + test server)
- Add to CI pipeline
