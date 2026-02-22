# Manual Intervention State for Tasks

**Status:** Idea
**Captured:** 2026-02-22

## Raw

> I wonder if it's worth having a manual-intervention state that tasks can be routed through to get to done. That way, at least there's an audit trail.

## Idea

Add a `resolved` (or `manual`) queue state that any task can be moved to directly, bypassing the normal flow transitions and hooks. This is for cases where a human completes work outside the normal agent flow — cherry-picks, direct commits, "task is no longer needed", etc.

Currently, manually completing a task requires faking the flow: clearing `blocked_by`, stepping through `incoming -> claimed -> provisional`, marking all hooks as complete, then using the `/accept` endpoint. This is fragile, leaves a misleading audit trail (it looks like an agent did the work), and requires knowledge of internal server endpoints.

### What it would look like

1. **Server**: Allow a `* -> resolved` transition from any queue state. No hooks required. Must include `resolved_by` (who) and `resolution_note` (why).

2. **Flow definition**: Add a wildcard transition in the flow schema:
   ```yaml
   transitions:
     "* -> resolved":
       type: manual
       requires:
         - resolved_by
         - resolution_note
   ```

3. **SDK**: `sdk.tasks.resolve(task_id, resolved_by="human", note="Cherry-picked from e985ade")`.

4. **Dashboard**: Resolved tasks show in Done tab with a distinct badge indicating manual resolution.

5. **Skill**: `/resolve-task <id> <reason>` for interactive use.

### Benefits

- Clean audit trail: "human resolved this at T with reason X"
- No fake hooks or flow gymnastics
- Works from any state (incoming, claimed, provisional, failed)
- Failed tasks can be resolved without re-running them

## Context

Came up while manually completing two tasks (109f0768, e7f566de) that were done via direct commits and cherry-picks. The server's strict state machine made it very painful — had to step through every flow transition, clear hooks, and use undocumented accept endpoints.

Also relevant for the 39 failed tasks in the queue — many are obsolete and should be resolvable with a note like "superseded by TASK-xxx" without going through the full flow.

## Open Questions

- Should `resolved` be a separate terminal state (like `done` and `failed`), or should resolved tasks just go to `done` with a `resolution` metadata field?
- Should there be a separate `cancelled` state for tasks that are abandoned vs completed manually?
- Should the server enforce any permissions on who can resolve tasks?

## Possible Next Steps

- Add `resolved` state to the server state machine (allows transition from any state)
- Add `resolved_by` and `resolution_note` columns to tasks table
- Add SDK method and `/resolve-task` skill
- Update dashboard to show resolution info
