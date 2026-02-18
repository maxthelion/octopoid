---
**Processed:** 2026-02-18
**Mode:** human-guided
**Actions taken:**
- Enqueued as TASK-c8953729 (SDK scope + scoped fixture + one test conversion) — DONE
- Extracted testing philosophy to CLAUDE.md (outside-in pyramid, scoped_sdk rules)
- Enqueued TASK-e8fd1b00 for migration steps 3-5 (convert tests, make mocks opt-in, CI)
**Outstanding items:** none — all remaining work tracked by TASK-e8fd1b00
---

# Scoped Local Server for Agent Testing

**Status:** Complete
**Captured:** 2026-02-17
**Related:** Draft 28 (outside-in testing strategy), Draft 25 (mock at transport level)

## Raw

> The reason for the octopoid server scope changes was in relation to draft 28, outside-in testing. The idea being that an agent could run tests against a locally running version of octopoid server scoped to its task. This wouldn't interfere with our production queue, nor conflict with other agents. We could remove some of the mocking that is potentially problematic. This might be the first task to tackle.

## Idea

Now that the server has `scope` support (every entity — tasks, projects, drafts — can be tagged with an optional `scope` field, and all list/claim endpoints filter by it), we can run tests against a real local server where each test session (or agent) gets its own scope. No mocking. No production interference. No cross-agent collisions.

### What exists today

**Server side (done):**
- Migration `0009_add_scope.sql` — `scope TEXT` column + index on tasks, projects, drafts
- All CRUD routes filter by `?scope=` query param
- Claim endpoint filters by `body.scope`
- Backwards compatible — scope is optional, defaults to NULL

**Test infrastructure (exists):**
- `packages/server/wrangler.test.toml` — local test server on port 9787
- `tests/integration/bin/start-test-server.sh` — starts server with migrations
- `tests/integration/conftest.py` — SDK fixture against localhost:9787

**What's missing:**
- Python SDK has no `scope` parameter — `OctopoidSDK` doesn't pass scope to any requests
- Unit tests mock `get_sdk()` at 4+ patch sites with `MagicMock` — fragile, hides real bugs
- No mechanism for a test to say "I want my own isolated slice of the queue"

### How it works

```
Test session starts:
  1. Ensure local server running (port 9787)
  2. Generate unique scope: "test-{uuid}" or "test-{test_name}-{timestamp}"
  3. Create SDK with scope: OctopoidSDK(server_url="http://localhost:9787", scope="test-abc123")
  4. All SDK calls automatically include scope parameter
  5. Test creates tasks, claims them, submits — all scoped
  6. Other tests / production data invisible
  7. Teardown: delete all entities with this scope (or just leave them — they're isolated)
```

### SDK changes

The SDK needs a `scope` field that's automatically included in all requests:

```python
class OctopoidSDK:
    def __init__(self, server_url, api_key=None, scope=None):
        self.server_url = server_url
        self.api_key = api_key
        self.scope = scope  # NEW: auto-included in all requests

class TasksAPI:
    def list(self, queue=None, role=None, ...):
        params = {...}
        if self._sdk.scope:
            params["scope"] = self._sdk.scope
        return self._request("GET", "/api/v1/tasks", params=params)

    def create(self, id, title, ...):
        body = {...}
        if self._sdk.scope:
            body["scope"] = self._sdk.scope
        return self._request("POST", "/api/v1/tasks", json=body)

    def claim(self, agent_name, orchestrator_id, ...):
        body = {...}
        if self._sdk.scope:
            body["scope"] = self._sdk.scope
        return self._request("POST", "/api/v1/tasks/claim", json=body)
```

Same pattern for `ProjectsAPI` and `DraftsAPI`.

### Test fixture

```python
# tests/conftest.py

import uuid
import pytest

@pytest.fixture(scope="session")
def test_server():
    """Ensure local test server is running."""
    # Could auto-start, or just check it's up
    url = "http://localhost:9787"
    # health check...
    return url

@pytest.fixture
def scoped_sdk(test_server):
    """SDK client scoped to this test — complete isolation."""
    from octopoid_sdk import OctopoidSDK
    scope = f"test-{uuid.uuid4().hex[:8]}"
    sdk = OctopoidSDK(server_url=test_server, scope=scope)
    yield sdk
    # Optional: cleanup tasks/drafts/projects with this scope
```

### What this replaces

The current `mock_sdk_for_unit_tests` fixture (conftest.py:132-173) patches `get_sdk()` at 4 separate import locations with a `MagicMock`:

```python
with patch('orchestrator.sdk.get_sdk', return_value=mock_sdk):
    with patch('orchestrator.tasks.get_sdk', return_value=mock_sdk):
        with patch('orchestrator.projects.get_sdk', return_value=mock_sdk):
            with patch('orchestrator.breakdowns.get_sdk', return_value=mock_sdk):
                yield mock_sdk
```

Problems with this:
- **Fragile patching** — adding a new `get_sdk()` import site means tests silently hit production
- **Fake return values** — `mock_sdk.tasks.claim.return_value = None` doesn't test real claim logic
- **Schema drift** — server changes fields, mock returns stale shapes, tests pass, production breaks
- **Never tests real lifecycle** — create→claim→submit→accept is never exercised against real validation

With scoped SDK, most of these tests can use a real server. The mock fixture becomes opt-in for the rare tests that genuinely need to test behavior when the SDK returns specific values (error cases, edge cases).

### Migration path

1. **Add `scope` to Python SDK** — small change, backwards compatible (scope=None → no filtering)
2. **Add `scoped_sdk` fixture** — new fixture alongside existing mock, doesn't break anything
3. **Convert tests one by one** — start with tests that are most affected by mock blindness (scheduler tests, lifecycle tests, queue_utils tests)
4. **Make mock fixture opt-in** — remove `autouse=True`, require tests that need mocks to explicitly request `mock_sdk_for_unit_tests`
5. **Add to CI** — start local server in CI pipeline before running tests

### What this enables beyond testing

Once the SDK has scope support, agents themselves could use it:
- Each agent gets `scope=TASK-xxx` in its environment
- Agent's SDK calls are scoped to its task
- Agent can create sub-tasks, drafts, etc. without polluting the main queue
- Orchestrator merges scoped results back when the agent finishes (pure function model)

This connects directly to draft #31 (pure functions): the agent operates in an isolated sandbox, the orchestrator handles integration with the real queue.

## Implementation: what to do first

The smallest useful change is **SDK scope support + scoped test fixture**. This unblocks everything else:

1. Add `scope` param to `OctopoidSDK.__init__()` and propagate to all API methods
2. Add `scoped_sdk` pytest fixture
3. Convert one test file (e.g., `test_hooks.py` which heavily uses `mock_sdk_for_unit_tests`) to use real scoped server
4. Verify it catches real bugs that mocks miss

## Open Questions

- Should scope be set per-SDK-instance (recommended) or per-request? Per-instance is simpler and matches "one scope per test" semantics.
- Should the test server auto-start via fixture, or require manual `npm run dev:test`? Auto-start is convenient but adds complexity.
- Should scoped data be cleaned up after tests, or left for debugging? Cleanup keeps the test DB small; leaving it helps debug failures.
- Does the local server need to run the same migrations as production? Yes — that's the whole point (catch schema drift).
