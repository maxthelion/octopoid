# Server-side task branch inheritance from project

## Context

When a task is created with a `project_id`, it should automatically inherit the project's `branch`. Currently the server just defaults to `'main'` — the caller has to explicitly pass `branch` every time.

This is part of the project branch sequencing feature. The rest of the wiring (lazy branch creation, auto-accept, worktree fetching) already exists in the orchestrator. This is the missing server-side piece.

## What to change

**File:** `submodules/server/src/routes/tasks.ts` — the POST `/api/v1/tasks` handler

**Current code (around line 142-158):**
The INSERT uses `body.branch || 'main'` directly.

**Required change:**
Before the INSERT, if `body.project_id` is set and `body.branch` is not explicitly provided (or is `'main'`), look up the project and inherit its branch:

```typescript
// Before the INSERT — inherit branch from project
if (body.project_id && (!body.branch || body.branch === 'main')) {
  const project = await queryOne<{ branch: string }>(
    db,
    'SELECT branch FROM projects WHERE id = ?',
    body.project_id
  )
  if (project?.branch) {
    body.branch = project.branch
  }
}
```

## Do NOT change

- Do not modify any Python code (orchestrator, queue_utils, etc.)
- Do not modify the README or CHANGELOG
- Do not add new endpoints — this is a change to the existing POST handler only

## Acceptance criteria

- [ ] POST `/api/v1/tasks` with `project_id` set and no `branch` → task gets the project's branch
- [ ] POST `/api/v1/tasks` with `project_id` set and explicit `branch` → explicit branch is used (not overridden)
- [ ] POST `/api/v1/tasks` without `project_id` → behaviour unchanged (defaults to 'main')
- [ ] Existing tests pass
