# Add paused field to tasks + update queue-status script

## Context

Currently there's no way to pause individual tasks. When we need to prevent a task from being claimed (e.g. during a refactor project), we hack `blocked_by` to point at an unrelated task. This is fragile — when the blocker completes, all hacked tasks suddenly unblock.

A proper `paused` boolean field on tasks would let us pause/unpause individual tasks cleanly. The scheduler's claim query would skip paused tasks, and the queue-status script would show them so we don't forget about them.

## Implementation

### 1. Add `paused` column to tasks table

**File:** `submodules/server/src/migrations/` (new migration)

```sql
ALTER TABLE tasks ADD COLUMN paused INTEGER NOT NULL DEFAULT 0;
```

### 2. Update claim query to skip paused tasks

**File:** `submodules/server/src/routes/tasks.ts`

The claim endpoint's SELECT query currently has:
```sql
WHERE queue = 'incoming' AND (blocked_by IS NULL OR blocked_by = '')
```

Add:
```sql
AND paused = 0
```

### 3. Update PATCH endpoint to allow setting paused

**File:** `submodules/server/src/routes/tasks.ts`

Add `paused` to the list of updatable fields in the PATCH handler.

### 4. Update SDK

**File:** `packages/python-sdk/octopoid_sdk/client.py`

Add `paused` parameter to `tasks.update()`.

### 5. Update queue-status script

**File:** `.claude/commands/queue-status.md`

Add a new section at the top or bottom of the output:

```
PAUSED (3 tasks)
  P1 | TASK-300fa689  | Fix broken lease monitor
  P1 | TASK-2b4f120f  | Add worktree sweeper
  P2 | TASK-e7198410  | Fix registration error
```

Query: `sdk.tasks.list(paused=True)` or filter incoming tasks where `paused=1`.

This ensures paused tasks are always visible — we don't forget about them.

### 6. Update shared types

**File:** `submodules/server/src/types/shared.ts`

Add `paused` to Task, CreateTaskRequest, UpdateTaskRequest types.

## Acceptance Criteria

- [ ] `paused` column exists on tasks table (default 0)
- [ ] Claim endpoint skips paused tasks
- [ ] PATCH endpoint can set/unset paused
- [ ] Python SDK supports `sdk.tasks.update(id, paused=True/False)`
- [ ] Queue-status script shows paused tasks in a dedicated section
- [ ] Existing tests pass
- [ ] Paused tasks don't appear as claimable in incoming list
