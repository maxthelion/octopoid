# Add admin endpoint to force tasks from failed to done

## Context

The default flow has no transitions out of `failed`. When a task's work is actually complete (PR merged, code on main) but the task is stuck in `failed` due to flow/fixer issues, there's no API way to resolve it — you have to delete the task entirely, losing its history.

This came up with task `6693d4d5` where the implementing agent completed all work and PR #270 was open and correct, but the task got stuck in a fixer loop and ended up in `failed` with no valid transitions.

See: `project-management/postmortems/2026-02-28-ghost-completion-no-pr-number.md`

## Requirement

Add a `POST /api/v1/tasks/:id/force-queue` endpoint that allows moving a task to any queue, bypassing flow transition validation.

Suggested behavior:
- Only works for tasks currently in `failed` (or make it fully general with a flag)
- Requires a `queue` field in the body (e.g. `{"queue": "done"}`)
- Requires a `reason` field explaining why the force-move is needed
- Stores the reason as a message on the task for audit trail
- Returns the updated task

This is an admin/escape-hatch operation — it should be clearly documented as bypassing normal flow guarantees.
