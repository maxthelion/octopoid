# Add POST /api/v1/tasks/{id}/requeue endpoint

## Context

The SDK has `sdk.tasks.requeue(task_id)` which calls `POST /api/v1/tasks/{id}/requeue`, but the server doesn't have this endpoint. The orchestrator's `_requeue_task()` currently uses `sdk.tasks.update(queue='incoming')` via PATCH, which may or may not be allowed by the state machine depending on the transition.

A dedicated requeue endpoint is needed because:
- PATCH with `queue` is blocked by the state machine for some transitions
- Requeue has specific semantics: clear `claimed_by`, clear `lease_expires_at`, optionally increment `attempt_count`
- The CLI `octopoid requeue <id>` command calls the SDK method which hits this missing endpoint

## Requirements

- `POST /api/v1/tasks/{id}/requeue` — moves a claimed task back to incoming
- Clears `claimed_by` and `lease_expires_at`
- Only allowed from `claimed` state (and possibly `provisional`)
- Returns the updated task
- Should bypass the normal state machine transition validation (requeue is a special operation, not a state transition)

## Who calls it

- `packages/python-sdk/octopoid_sdk/client.py` — `TasksAPI.requeue()`
- `orchestrator/cli.py` — `cmd_requeue`
- `tests/integration/test_flow.py` — 2 integration tests
