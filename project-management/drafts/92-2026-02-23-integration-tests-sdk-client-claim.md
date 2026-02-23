# Add integration tests for SDK client.py: tasks.claim() returns None on empty queue and max_claimed

**Status:** Idea
**Author:** testing-analyst
**Captured:** 2026-02-23

## Gap

`packages/python-sdk/octopoid_sdk/client.py` has **zero tests**. It is the foundational SDK client used by the entire orchestrator — every scheduler operation, every agent lifecycle transition, every queue state change goes through it. Despite being the critical layer between the orchestrator and the server, it has no test coverage at all.

The most risky untested behaviour is in `TasksAPI.claim()`:

```python
try:
    return self.client._request('POST', '/api/v1/tasks/claim', json=data)
except requests.HTTPError as e:
    if e.response.status_code in (404, 429):
        return None
    raise
```

This method returns `None` on 404 (empty queue) and 429 (max_claimed limit hit), but raises on all other errors. The scheduler's main loop depends entirely on this contract: it checks `if task is None` to decide whether to wait or spawn an agent. If this exception-swallowing behaviour were accidentally removed or broken (e.g. a refactor changes which status codes are silently ignored), the scheduler would crash with an unhandled `HTTPError` every time the queue is empty — which is the normal steady state.

## Proposed Test

Add integration tests in `tests/integration/test_sdk_client.py` using the `scoped_sdk` fixture against the real test server (localhost:9787):

**Test 1 — `claim()` returns None on empty queue:**
- Create a `scoped_sdk` instance
- Call `sdk.tasks.claim(orchestrator_id="test-orch", agent_name="test-agent")` without creating any tasks first
- Assert the return value is `None` (server returns 404, client converts to None)

**Test 2 — `claim()` returns a task when one is available:**
- Create a task in the `incoming` queue via `sdk.tasks.create()`
- Call `sdk.tasks.claim(orchestrator_id="test-orch", agent_name="test-agent")`
- Assert the returned task has `queue="claimed"` and matches the created task ID

**Test 3 — `claim()` returns None when max_claimed limit is reached:**
- Register an orchestrator with `max_claimed=1`
- Create two tasks in `incoming`
- Claim the first task (succeeds, returns task)
- Attempt to claim the second task with `max_claimed=1`
- Assert the second `claim()` returns `None` (server returns 429)

## Why This Matters

The scheduler loop runs every few seconds and calls `tasks.claim()` continuously. The `None`-on-404/429 contract is load-bearing: without it, a quiet queue would crash the orchestrator. This code path is exercised thousands of times per day in production but never exercised in tests. A routine refactor of the exception-handling logic in `_request()` or `claim()` could silently break this, causing the orchestrator to stop working entirely when no tasks are available — exactly the normal idle state.

The `scoped_sdk` fixture already handles test isolation perfectly (each test gets its own `scope` on the server), so adding these tests requires no new infrastructure.
