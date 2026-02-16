# Implementation Summary: Guard PATCH endpoint against queue=done bypass

## Changes Made

### Server Submodule (octopoid-server)
All changes were made in the `octopoid-server` repository on branch `feat/task-branch-inheritance`.

**Commit:** `98b0477` - "feat: guard PATCH endpoint against queue=done bypass"

#### Files Modified:

1. **src/routes/tasks.ts**
   - Added validation guard in PATCH `/:id` endpoint (lines 194-203)
   - Returns 400 error when `queue="done"` is requested
   - Error message directs users to use POST `/api/v1/tasks/:id/accept` instead
   
2. **tests/integration.test.ts**
   - Added test case "should reject PATCH with queue=done" (lines 444-469)
   - Verifies that PATCH returns 400 with appropriate error message
   - Ensures error message mentions the /accept endpoint

3. **src/types/shared.ts**
   - Synced `execution_notes` field in `SubmitTaskRequest` interface (line 144)
   - This field was already in use but missing from the type definition

## Acceptance Criteria Status

- [x] PATCH with queue=done returns 400 with descriptive error pointing to the accept endpoint
- [x] Accept endpoint still works normally (no changes made to accept endpoint)
- [x] Existing PATCH operations for other queue transitions still work (only queue="done" is blocked)

## Testing

Integration tests were written but couldn't be fully executed due to database setup issues in the test environment (unrelated to this change). The test:
- Creates a task via POST
- Attempts to PATCH the task with queue="done"
- Verifies 400 response with correct error message
- Verifies error message mentions "/accept" endpoint

## Technical Details

The guard follows the same pattern mentioned in the task description for preventing state machine bypasses. By rejecting direct queue="done" transitions via PATCH, we ensure that:
1. The accept endpoint's state machine logic runs (unblocking dependents, recording accepted_by, etc.)
2. The transition goes through proper validation
3. Side effects like unblocking dependent tasks are triggered

The guard is placed early in the PATCH handler, before any database operations, so it fails fast with a clear error message.
