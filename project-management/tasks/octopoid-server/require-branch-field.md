# Require `branch` field on project and task creation

## Context

`POST /api/v1/projects` and `POST /api/v1/tasks` both accept `branch: null` without complaint. The client (`create_task()`) happens to default to `main`, but the server has no validation — so any direct SDK call, curl, or future client can create branchless records.

This caused a real incident: a project was created with `branch=null`, all 4 child tasks completed successfully, but `check_project_completion` silently skipped it because there was no branch to create a PR from. Work was done but never landed. See `project-management/postmortems/2026-02-23-project-branch-null-silent-failure.md`.

## Change

Make `branch` a required field on both endpoints. Return 400 if missing or empty.

### `POST /api/v1/projects`

Currently:
```typescript
if (!body.id || !body.title) {
  return c.json({ error: 'Missing required fields: id, title' }, 400)
}
```

Change to:
```typescript
if (!body.id || !body.title || !body.branch) {
  return c.json({ error: 'Missing required fields: id, title, branch' }, 400)
}
```

### `POST /api/v1/tasks`

Add the same validation — reject if `branch` is missing or empty.

## Not in scope

- No server-side defaulting. The client is responsible for choosing the right branch. The server just enforces that one is provided.
- No changes to PATCH/PUT endpoints — only creation.
