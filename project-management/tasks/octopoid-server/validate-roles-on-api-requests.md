# Validate role names against registered roles on API requests

**Priority:** P1

## Context

The server has a `roles` table where orchestrators register their valid roles at startup. But the server doesn't currently validate role names on task creation or claim requests against this table. This means typos or mismatched role names silently succeed, leading to tasks that can never be claimed.

Example: if an orchestrator registers role `implementer` but a task is created with `role=implement`, the claim endpoint will silently return no matches because the filter doesn't match. There's no error telling you the role doesn't exist.

## What to Do

### 1. Validate role on task creation

When `POST /api/v1/tasks` includes a `role` field, check it against the `roles` table. If the role doesn't exist, return 400 with a clear error:

```json
{
  "error": "Unknown role 'implment'. Registered roles: implementer, gatekeeper, breakdown"
}
```

### 2. Validate role_filter on claim

When `POST /api/v1/tasks/claim` includes a `role_filter`, check it against the `roles` table. If it doesn't match a registered role, return 400 with the same style of error.

### 3. Backwards compatibility

If NO roles are registered (empty `roles` table), skip validation entirely. This preserves the current behavior for orchestrators that haven't upgraded to register roles yet.

## Acceptance Criteria

- [ ] Task creation with an unregistered role returns 400 with helpful error listing registered roles
- [ ] Task claim with an unregistered role_filter returns 400 with helpful error
- [ ] If no roles are registered, all role strings are accepted (backwards compatible)
- [ ] Existing integration tests still pass
- [ ] Add tests for the validation behavior
