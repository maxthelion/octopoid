# Add POST /api/v1/tasks/{id}/force-queue admin endpoint

## Context

Tasks occasionally end up in `failed` when the work is actually done (PR merged,
task accepted). The server blocks `PATCH` to `done` and `/accept` requires a valid
flow transition that doesn't exist from `failed`. There is currently no clean recovery
path once a task is wrongly in `failed`.

A force-queue endpoint allows an administrator to move a task to any queue, bypassing
flow validation. This is an **admin-only** escape hatch for situations where the state
machine is wrong — e.g. a post-merge step failure that incorrectly moved an already-done
task to `failed`.

## Requirements

### Endpoint

```
POST /api/v1/tasks/{id}/force-queue
```

### Request body

```json
{
  "queue": "done",
  "reason": "Task was completed but incorrectly moved to failed by post-merge step"
}
```

- `queue` (required): Target queue name. Any valid queue is accepted (no state machine
  validation). Common targets: `done`, `incoming`, `failed`, `provisional`.
- `reason` (required): Human-readable explanation for the audit log.

### Behaviour

1. Fetch the task by `{id}`. Return 404 if not found.
2. Verify the caller has admin scope (not just standard orchestrator scope).
3. Update `queue` to the requested value — bypassing all flow/state-machine validation.
4. Write an audit log entry: `{ task_id, from_queue, to_queue, reason, timestamp, actor }`.
5. Return the updated task (same shape as `GET /api/v1/tasks/{id}`).

### Error responses

- `404` — task not found
- `403` — caller lacks admin scope
- `400` — missing or invalid `queue` / `reason` fields

### Authorization

Use the existing admin-scope mechanism. If no admin scope exists yet, add a new scope
`admin` that is granted only to admin API keys. Standard orchestrator keys must not be
able to call this endpoint.

## SDK surface

Add to `packages/python-sdk/octopoid_sdk/client.py` in `TasksAPI`:

```python
def force_queue(self, task_id: str, queue: str, reason: str) -> dict:
    """Force a task to a specific queue, bypassing flow validation (admin only)."""
    return self._client.post(
        f"/api/v1/tasks/{task_id}/force-queue",
        json={"queue": queue, "reason": reason},
    )
```

## Who will use it

- `octopoid/queue_utils.py` — manual recovery operations
- CLI: `octopoid force-queue <id> <queue> --reason "..."` (new command, add to `cli.py`)
- Admins recovering tasks stuck in `failed` after post-merge step failures

## Related

- Draft #169: `project-management/drafts/169-2026-02-27-failed-queue-recovery.md`
- Orchestrator fix: `octopoid/result_handler.py` — catch-all now checks done before failing
- Orchestrator fix: `octopoid/tasks.py` — `fail_task()` refuses to overwrite done queue
