# Server-Registered Roles

**Source:** Draft #38 (`project-management/drafts/38-2026-02-18-shared-role-enums.md`)

## Context

Role identifiers are loose strings defined in three places (Python orchestrator, TypeScript server, agent YAML configs) with no shared source of truth. The server currently hardcodes a `TaskRole` type:

```typescript
// src/types/shared.ts line 34
export type TaskRole = 'implement' | 'breakdown' | 'test' | 'review' | 'fix' | 'research'
```

This caused 69 consecutive claim failures when agent roles ("implementer") didn't match task roles ("implement"). The fix: the server shouldn't define roles at all. Roles are data registered by orchestrators, not server code.

## What to Build

### 1. Migration: `0008_add_roles.sql`

```sql
CREATE TABLE IF NOT EXISTS roles (
    name TEXT PRIMARY KEY,
    description TEXT,
    claims_from TEXT DEFAULT 'incoming',
    orchestrator_id TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (orchestrator_id) REFERENCES orchestrators(id)
);
```

### 2. New route: `src/routes/roles.ts`

Follow the pattern in `src/routes/orchestrators.ts`. Create a Hono sub-app with these endpoints:

#### `POST /register` — Bulk upsert roles

Called by orchestrators on startup to register the roles their agents handle.

```typescript
// Request body:
{
  "orchestrator_id": "cluster-machine",
  "roles": [
    { "name": "implement", "claims_from": "incoming", "description": "Code implementation" },
    { "name": "review", "claims_from": "provisional", "description": "Code review" }
  ]
}
```

Logic:
- Validate `orchestrator_id` exists in `orchestrators` table (return 400 if not)
- For each role: upsert (INSERT OR REPLACE) into `roles` table
- Return 200 with the list of registered roles

This is idempotent — calling it again with the same data is a no-op.

#### `GET /` — List all registered roles

```typescript
// Response:
{
  "roles": [
    { "name": "implement", "claims_from": "incoming", "orchestrator_id": "...", "created_at": "..." },
    ...
  ]
}
```

#### `GET /:name` — Get a specific role

Returns 404 if not found.

### 3. Register routes in `src/index.ts`

```typescript
import { rolesRoute } from './routes/roles'
app.route('/api/v1/roles', rolesRoute)
```

### 4. Validate role on task creation (BACKWARDS COMPATIBLE)

In `src/routes/tasks.ts`, in the `POST /` handler (around line 119), add role validation **only if roles have been registered**:

```typescript
// After extracting body.role:
if (body.role) {
  const registeredRoles = await db.prepare('SELECT COUNT(*) as count FROM roles').first<{count: number}>()
  if (registeredRoles && registeredRoles.count > 0) {
    // Roles table has entries — validate against it
    const role = await db.prepare('SELECT name FROM roles WHERE name = ?').bind(body.role).first()
    if (!role) {
      return c.json({ error: `Unknown role '${body.role}'. Registered roles: ${await getRegisteredRoleNames(db)}` }, 400)
    }
  }
  // If roles table is empty, allow any role string (backwards compatible)
}
```

This is the key backwards compatibility mechanism: if no orchestrator has registered roles yet, the server accepts any role string (current behaviour). Once an orchestrator registers roles, validation kicks in.

### 5. Optional: Look up `claims_from` in claim endpoint

In the claim endpoint (`POST /claim`, around line 355), if a `role_filter` is provided, optionally look up the role's `claims_from` value to determine which queue to search:

```typescript
// Only if no explicit queue override in the claim request:
if (body.role_filter && !body.queue) {
  const role = await db.prepare('SELECT claims_from FROM roles WHERE name = ?').bind(body.role_filter).first()
  if (role?.claims_from) {
    claimQueue = role.claims_from
  }
}
```

This is additive — if the claim request specifies a queue explicitly, use that. If not, fall back to the role's `claims_from`, then to the default `'incoming'`.

## What NOT to Change

- Don't remove the `TaskRole` type yet — just stop enforcing it in validation. The type can be removed in a follow-up once orchestrators are registering roles.
- Don't change any existing endpoint signatures or response shapes.
- Don't change the `role` column in the `tasks` table — it stays as `TEXT`.
- Don't change how `role_filter` works in the claim endpoint — it still does exact string matching against `task.role`.

## Backwards Compatibility Contract

1. **No roles registered = current behaviour.** All existing functionality works unchanged.
2. **Roles registered = validation on task creation.** Only task creation gets stricter; claims, submits, etc. are unaffected.
3. **New endpoints are additive.** `POST /api/v1/roles/register` and `GET /api/v1/roles` are new paths that don't conflict with anything.
4. **`claims_from` lookup is optional.** Claim requests that specify a queue explicitly still work. The lookup only applies when no queue is specified.

## Testing

Add tests alongside existing patterns. The integration test suite at `tests/integration/` exercises the server on port 9787.

Suggested tests:
- Register roles, list them, get by name
- Create task with valid registered role — succeeds
- Create task with invalid role (when roles are registered) — 400
- Create task with any role (when no roles registered) — succeeds (backwards compat)
- Claim with role_filter uses role's claims_from when no queue specified

## Files to Create/Modify

| File | Action |
|------|--------|
| `migrations/0008_add_roles.sql` | Create — roles table |
| `src/routes/roles.ts` | Create — register, list, get endpoints |
| `src/index.ts` | Modify — add roles route |
| `src/routes/tasks.ts` | Modify — add role validation to POST handler |
| `src/routes/tasks.ts` | Modify — optional claims_from lookup in claim handler |
