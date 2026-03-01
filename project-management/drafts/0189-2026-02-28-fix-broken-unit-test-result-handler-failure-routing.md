# Fix broken unit test and add integration test for result_handler failure routing

**Author:** testing-analyst
**Captured:** 2026-02-28

## Gap

`octopoid/result_handler.py` (`_handle_fail_outcome`) was recently fixed to route all
agent failures through `fail_task()` → `requires-intervention`, not directly to `failed`.
However the corresponding unit test (`test_failed_outcome_moves_to_failed` in
`octopoid/tests/test_scheduler_lifecycle.py:272`) **is broken**: it mocks
`octopoid.result_handler.queue_utils` but `fail_task()` in `tasks.py` calls `get_sdk()`
independently, so the test actually hits the production API with a fake task ID and gets
a `400` error. There is also **no integration test** that verifies the new invariant —
that a failed agent routes to `requires-intervention` (not directly to `failed`) on first
failure.

## Failing Test Evidence

Running the test currently produces:
```
FAILED octopoid/tests/test_scheduler_lifecycle.py::TestHandleAgentResultFailed::test_failed_outcome_moves_to_failed
requests.exceptions.HTTPError: 400 Client Error: Bad Request for url:
  https://octopoid-server.maxthelion.workers.dev/api/v1/tasks/test123
```

The test patches `octopoid.result_handler.queue_utils` but not
`octopoid.result_handler.fail_task`, so the real `fail_task()` fires and
calls the live production server.

## Proposed Fix and Test

### 1. Fix the broken unit test

Patch `fail_task` directly in the result_handler module:

```python
# Before (broken — patches the wrong thing):
with patch("octopoid.result_handler.queue_utils") as mock_qu, \
     patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "failed", ...}):
    mock_qu.get_sdk.return_value = mock_sdk
    handle_agent_result("test123", "agent-1", tmp_task_dir)
    mock_sdk.tasks.update.assert_called_once_with("test123", queue="failed")  # old behaviour

# After (correct — patches the call that actually happens):
with patch("octopoid.result_handler.fail_task") as mock_fail_task, \
     patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "failed", ...}):
    mock_sdk.tasks.get.return_value = sample_task
    with patch("octopoid.result_handler.queue_utils") as mock_qu:
        mock_qu.get_sdk.return_value = mock_sdk
        handle_agent_result("test123", "agent-1", tmp_task_dir)
    mock_fail_task.assert_called_once_with("test123", reason="Tests don't pass", source="agent-outcome-failed")
```

### 2. Add integration test for failure→intervention routing

In `tests/integration/test_scheduler_mock.py`, add a test that:
- Creates a real task (via `scoped_sdk`)
- Claims it and runs a mock agent that writes `outcome: failed` to stdout.log
- Calls `handle_agent_result()` against the real local server
- Asserts the task is now in `requires-intervention` (not `failed`)

Use the existing `scoped_sdk` fixture and mock-agent pattern from `test_scheduler_mock.py`.

### 3. Add integration test for second-failure→failed routing

Second scenario: a task already in `requires-intervention` fails again.
- Create task in `requires-intervention` state
- Call `handle_agent_result()` with `outcome=failed`
- Assert task ends up in `failed` (terminal state)

## Why This Matters

The `_handle_fail_outcome` path is the critical self-correcting-failure invariant of the
system — all agent failures must route through intervention before being declared terminal
failures. This was recently fixed (commit `1fdfac4`) but:

1. **The test that should guard this invariant is broken** — it hits production with fake data.
2. **The actual new invariant (→ requires-intervention) is untested**.
3. **The integration test** would catch regressions in the `fail_task()` call chain
   (result_handler → tasks.fail_task → request_intervention → real server).

If `_handle_fail_outcome` regresses (e.g. someone removes the `fail_task()` call and
goes back to `sdk.tasks.update(queue="failed")`), the broken unit test would still pass
(because it patches `fail_task`) while only an integration test catches the missing
intervention step.
