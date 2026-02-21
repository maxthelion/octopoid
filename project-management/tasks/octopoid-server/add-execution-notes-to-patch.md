# Add execution_notes to PATCH /api/v1/tasks/:id endpoint

## Context

The orchestrator already sends `execution_notes` in `sdk.tasks.update()` calls (which maps to PATCH) when moving a task to `failed`. For example:

```python
sdk.tasks.update(
    task_id,
    queue="failed",
    execution_notes="Circuit breaker: lease expired 3 time(s)...",
)
```

However, the server's PATCH handler only processes fields in a hardcoded `fields` list (routes/tasks.ts), which currently does not include `execution_notes`. As a result, the failure reason is silently dropped — the `execution_notes` field on the task stays null even when a meaningful error message is provided.

`execution_notes` is already stored by the `/submit` endpoint and is part of the `Task` schema. It just needs to be added to the PATCH endpoint's allowed fields.

## Change Required

In `src/routes/tasks.ts`, in the PATCH handler's `fields` array (around line 215), add `'execution_notes'` to the list:

```typescript
const fields = [
  'title',
  'queue',
  // ... existing fields ...
  'execution_notes',  // add this
]
```

Also update `UpdateTaskRequest` in `src/types/shared.ts` to include:

```typescript
execution_notes?: string | null
```

## Acceptance Criteria

- [ ] PATCH /api/v1/tasks/:id accepts `execution_notes` field
- [ ] The field is persisted to the database
- [ ] `UpdateTaskRequest` type includes `execution_notes`
- [ ] Existing submit endpoint behavior is unchanged
