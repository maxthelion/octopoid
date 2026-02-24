# Fix PATCH /api/v1/tasks/:id to handle lease_expires_at

**Priority:** P1

## Context

The PATCH endpoint for tasks silently ignores `lease_expires_at` in the request body. It handles `claimed_by`, `queue`, `execution_notes`, and other fields — but `lease_expires_at` is not in the allowlist of updatable fields.

This causes a real bug: the orchestrator's `check_and_requeue_expired_leases()` calls `sdk.tasks.update(task_id, claimed_by=null, lease_expires_at=null)` to clear an expired claim. The server clears `claimed_by` but leaves `lease_expires_at` set. The task ends up with a ghost lease — no claimer, but a stale expiry timestamp that may confuse other logic.

## Reproduction

```bash
# Claim a task with a short lease
POST /api/v1/tasks/claim { lease_duration_seconds: 60 }

# Try to clear the lease
PATCH /api/v1/tasks/:id { "claimed_by": null, "lease_expires_at": null }

# Result: claimed_by is cleared, lease_expires_at is NOT cleared
GET /api/v1/tasks/:id → { "claimed_by": null, "lease_expires_at": "2026-02-24T..." }
```

## Fix

Add `lease_expires_at` to the PATCH handler's field processing. When the value is `null`, set the column to NULL. When it's a string, validate it as an ISO timestamp and store it.

Same treatment as `claimed_by` — nullable, clearable via null in the PATCH body.

## Failing tests

Two integration tests in the orchestrator repo demonstrate this bug:

- `tests/integration/test_lease_recovery.py::TestLeaseRecoveryProvisional::test_expired_provisional_lease_is_cleared`
- `tests/integration/test_lease_recovery.py::TestLeaseRecoveryProvisional::test_stale_lease_without_claimed_by_is_still_cleared`

Both will pass once the server fix is deployed.

## Acceptance Criteria

- [ ] PATCH /api/v1/tasks/:id accepts `lease_expires_at` in the body
- [ ] Setting `lease_expires_at: null` clears the column to NULL
- [ ] Setting `lease_expires_at: "<ISO string>"` updates the column
- [ ] Integration test: claim a task, PATCH with `lease_expires_at: null`, verify it's cleared on GET
