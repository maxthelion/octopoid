# Guard PATCH endpoint against state machine bypass

## Problem

The `PATCH /api/v1/tasks/:id` endpoint allows setting `queue` directly via raw SQL update, bypassing the state machine transitions and their side effects (guards, history recording, unblock_dependents, lease management).

This caused a production bug: a task was moved to `done` via `sdk.tasks.update(queue='done')` instead of the `accept` endpoint, so `unblock_dependents` never fired and 12 downstream tasks were stuck indefinitely.

## File to change

`submodules/server/src/routes/tasks.ts` — the `PATCH /:id` handler (around line 188).

## What to do

If the PATCH body includes a `queue` field, reject the request with a 400 error telling the caller to use the appropriate dedicated endpoint instead. The error message should list the correct endpoints:

- `incoming → claimed`: `POST /tasks/claim`
- `claimed → provisional`: `POST /tasks/:id/submit`
- `provisional → done`: `POST /tasks/:id/accept`
- `provisional → incoming`: `POST /tasks/:id/reject`
- `claimed → incoming`: `POST /tasks/:id/requeue` (if it exists, otherwise note it)
- `claimed → failed`: `POST /tasks/:id/fail` (if it exists)

Implementation: at the top of the PATCH handler, before building the SET clause, check if `'queue' in body`. If so, return:

```json
{
  "error": "Cannot update queue directly via PATCH. Use the dedicated transition endpoints (e.g. /tasks/:id/accept, /tasks/:id/submit) to ensure state machine guards and side effects run correctly."
}
```

Also remove `'queue'` from the `fields` array so it can't sneak through.

## Exceptions to consider

The `blocked_by` field is also set via PATCH to clear blocking dependencies (as a manual unblock). This is fine to keep — it doesn't need state machine transitions. But add a comment noting that `blocked_by` updates via PATCH are intentional for manual unblocking.

## Acceptance criteria

- [ ] PATCH rejects requests that include `queue` in the body with a 400 and helpful error message
- [ ] `queue` is removed from the allowed fields array
- [ ] Existing tests pass
- [ ] A comment explains why `queue` is excluded
