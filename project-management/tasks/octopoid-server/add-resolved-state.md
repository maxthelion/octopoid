# Add resolved_by and resolution_note fields for resolved queue state

## Context

The orchestrator now supports a `resolved` queue state — tasks can be moved there
via PATCH with `queue=resolved`. However, the tasks table has no `resolved_by` or
`resolution_note` columns, so resolution metadata is silently dropped by the server.

The orchestrator's `resolve_task()` function passes these fields when resolving tasks,
and they need to be persisted so the dashboard and audit trail reflect who resolved a
task and why.

## Changes Required

### 1. Migration — add columns

Create `migrations/000X_add_resolved_fields.sql`:

```sql
-- Add resolved_by and resolution_note for tasks manually resolved by humans
ALTER TABLE tasks ADD COLUMN resolved_by TEXT;
ALTER TABLE tasks ADD COLUMN resolution_note TEXT;
```

### 2. PATCH handler — allow new fields

In `src/routes/tasks.ts`, add to the `fields` array in the PATCH handler (around line 215):

```typescript
const fields = [
  // ... existing fields ...
  'resolved_by',
  'resolution_note',
]
```

### 3. Type definitions

In `src/types/shared.ts`, add to `UpdateTaskRequest`:

```typescript
resolved_by?: string | null
resolution_note?: string | null
```

And add to the `Task` type:

```typescript
resolved_by?: string | null
resolution_note?: string | null
```

### 4. POST /resolve endpoint (optional — bonus)

Add a dedicated endpoint `POST /api/v1/tasks/:id/resolve` that accepts
`{ resolved_by, resolution_note }` and transitions the task to `resolved`,
similar to how `/accept` handles the `done` transition. This provides a cleaner
interface than PATCH but is secondary to the field persistence fix.

## Acceptance Criteria

- [ ] `resolved_by TEXT` column added to tasks table (migration applied to D1)
- [ ] `resolution_note TEXT` column added to tasks table (migration applied to D1)
- [ ] PATCH /api/v1/tasks/:id accepts and persists `resolved_by` field
- [ ] PATCH /api/v1/tasks/:id accepts and persists `resolution_note` field
- [ ] `Task` type includes `resolved_by` and `resolution_note` fields
- [ ] `UpdateTaskRequest` type includes these optional fields
