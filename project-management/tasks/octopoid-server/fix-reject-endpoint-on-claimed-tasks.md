# Fix reject endpoint returning 409 on claimed tasks

## Problem

`POST /api/v1/tasks/{id}/reject` returns 409 Conflict when the task is claimed. This breaks all gatekeeper rejections:

1. Implementer finishes, task moves to `provisional`
2. Gatekeeper claims the task (task moves to `claimed`)
3. Gatekeeper reviews, decides to reject
4. `POST /tasks/{id}/reject` returns 409
5. Error handler moves task to `requires-intervention` — stuck

## Root cause

Line 899 of the server code. When no flow is registered, the fallback logic requires the task to be in `provisional` for reject to work. But the gatekeeper has already claimed the task, moving it to `claimed`. The reject endpoint doesn't accept `claimed` as a valid source queue.

## Current impact

Every gatekeeper rejection fails. At least 4 tasks are stuck in `requires-intervention` because of this (1ea7b68d, ccc41941, 72ff0d4e, a80d12ff).

## Expected behaviour

The reject endpoint should work on tasks in `claimed` (at minimum when claimed by a gatekeeper). The gatekeeper's workflow is: claim → review → reject/accept. Reject is a lifecycle transition, not a content change.

The reject endpoint should also clear `claimed_by` as part of the transition, since the task is being sent back to `incoming` for another agent to pick up.

## Fix

Update the reject endpoint's queue validation (around line 899) to accept tasks in `claimed` as well as `provisional`. When rejecting from `claimed`, clear `claimed_by` and move the task to `incoming`.
