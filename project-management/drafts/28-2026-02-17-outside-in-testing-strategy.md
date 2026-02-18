---
**Processed:** 2026-02-18
**Mode:** human-guided
**Actions taken:**
- Extracted testing philosophy (outside-in pyramid, scoped_sdk rules) to CLAUDE.md
- Concrete implementation was already absorbed by Draft 32 (scoped local server) → TASK-c8953729 (done)
**Outstanding items:** none — remaining migration steps (convert tests, CI) tracked by Draft 32
---

# Outside-In Testing Strategy with Local Multi-Tenant Server

**Status:** Complete
**Captured:** 2026-02-17
**Related:** Draft 27 (Scheduler integration tests)

## Raw

> We should codify our testing approach to be outside in. Prioritise end to end testing. One issue we've experienced though is that we mock the server to avoid spamming production. We might need a local server for testing, with each agent scoping their queries somehow. Eg a multi-tenant octopoid-server.

## Idea

Adopt an outside-in testing philosophy: prioritise end-to-end tests that exercise real code paths, avoid mocks unless absolutely necessary. The current unit-test-heavy approach has failed to catch critical regressions (draft #27: `guard_claim_task` deletion broke all implementers, 50+ failures undetected).

### The mock problem

Tests currently mock `get_sdk()` to avoid hitting the production server. This is correct — tests shouldn't spam production. But the mocks hide real bugs:

- Server schema changes break the SDK, but mocked tests pass
- API validation changes (like the branch NOT NULL constraint) are invisible to tests
- The full lifecycle (create → claim → submit → accept) is never tested against real server logic

### Solution: local test server

Run the Cloudflare Workers server locally for tests using `wrangler dev`. We already have this infrastructure:

- `packages/server/wrangler.test.toml` — test config on port 9787
- `tests/integration/bin/start-test-server.sh` — starts server with migrations
- `tests/integration/conftest.py` — SDK fixture pointing at localhost:9787

The gap: this server uses a single shared D1 database. Concurrent test runs or parallel agents collide.

### Multi-tenancy for test isolation

Each test run (or agent) needs its own scope. Options:

1. **Orchestrator ID scoping** — Already exists. Each SDK instance registers an orchestrator ID. Server queries could filter by `orchestrator_id`. Tests register a unique orchestrator per test session.

2. **Namespace prefix** — Task IDs already use `TASK-{uuid}`. Tests could use `TEST-{session}-{uuid}` and clean up by prefix. Simple but fragile.

3. **Separate D1 databases** — Wrangler supports multiple D1 bindings. Each test run gets a fresh database. Clean isolation but slower setup.

4. **In-memory SQLite** — Replace D1 with an in-memory SQLite for tests. Fastest, but diverges from production (D1 has quirks).

Option 1 (orchestrator ID scoping) is probably the right fit — it's already in the schema, just needs server-side query filtering and test fixtures that register unique orchestrators.

### Testing pyramid (outside-in)

```
Priority 1: End-to-end (scheduler + real server + real SDK)
  - Task lifecycle: create → claim → spawn → submit → accept
  - Guard chain: full pipeline with real state
  - Worktree creation with real git

Priority 2: Integration (real server, mocked spawn)
  - API contract tests (SDK against real server)
  - Server migration correctness
  - Flow state machine transitions

Priority 3: Unit (mocked dependencies)
  - Pure logic (parsing, formatting, config merging)
  - Edge cases that are hard to trigger end-to-end
  - NOT: testing that function A calls function B with mocks
```

### What changes

- **Default to real server for tests.** `conftest.py` starts local server if not running.
- **Stop mocking `get_sdk()`** in most tests. Only mock for true unit tests of pure logic.
- **Each test session gets a unique orchestrator ID** for isolation.
- **CI runs the local server** as part of the test pipeline.

## Context

The current test suite (28 scheduler unit tests, 27 integration tests) didn't catch `guard_claim_task` being deleted because:
- Unit tests mock everything, so deleting real code doesn't fail tests
- Integration tests only cover the server API, not the scheduler
- No test exercises the critical path: task in queue → scheduler claims → agent spawns

## Open Questions

- Can wrangler dev run in CI (GitHub Actions)? Need Node.js + wrangler installed.
- How to handle the test server lifecycle? pytest fixture with session scope? Separate process?
- Should we keep any pure unit tests, or go fully outside-in?
- Performance: how slow is starting a local wrangler server per test session?

## Possible Next Steps

- Extend orchestrator ID scoping in server queries (filter tasks by orchestrator_id)
- Add pytest fixture that auto-starts local server and registers a session-scoped orchestrator
- Convert existing mocked tests to use real server where possible
- Write the critical-path end-to-end test (draft #27, Level 3)
- Add to CLAUDE.md or DEVELOPMENT_RULES.md as the testing philosophy
