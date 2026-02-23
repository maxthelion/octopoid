# Accept endpoint should respect flow definitions, not hardcode `provisional`

## Problem

The `POST /tasks/:id/accept` endpoint hardcodes `task.queue !== 'provisional'` as a guard (line 801 of `src/routes/tasks.ts`). This means tasks can only be accepted from the `provisional` queue, regardless of what the registered flow defines.

With custom flows, a user might register a flow like:

```
incoming → claimed → testing → staging → done
```

But accepting from `staging` fails with `409: Invalid transition: task is in staging, expected provisional`. The only workaround is to insert `provisional` as a mandatory waypoint before `done`, which defeats the purpose of custom flows.

## Root Cause

The accept endpoint was written before the extensible flow system. It assumes the standard `incoming → claimed → provisional → done` lifecycle. The PATCH endpoint already validates against the registered flow (line 316), but accept/reject/requeue still have hardcoded state guards.

## Proposed Fix

In the accept handler, replace:

```typescript
if (task.queue !== 'provisional') {
  return c.json(
    { error: 'Failed to accept task', details: [`Invalid transition: task is in ${task.queue}, expected provisional`] },
    409
  )
}
```

With flow-aware validation:

```typescript
// Look up the task's registered flow
const flow = await queryOne(db, 'SELECT * FROM flows WHERE name = ? AND scope = ?', task.flow || 'default', task.scope)
if (flow) {
  const transitions = JSON.parse(flow.transitions)
  const canTransitionToDone = transitions.some(t => t.from === task.queue && t.to === 'done')
  if (!canTransitionToDone) {
    return c.json(
      { error: 'Failed to accept task', details: [`No transition from ${task.queue} to done in flow ${task.flow}`] },
      409
    )
  }
} else {
  // No custom flow: fall back to requiring provisional (backwards compat)
  if (task.queue !== 'provisional') {
    return c.json(
      { error: 'Failed to accept task', details: [`Invalid transition: task is in ${task.queue}, expected provisional`] },
      409
    )
  }
}
```

Same pattern should be applied to reject (line 867) and requeue (line 920).

The `WHERE queue = 'provisional'` in the atomic UPDATE SQL (line 816) also needs to be parameterised to use the actual current queue.

## Test

`tests/integration/test_custom_queue_flows.py::TestCustomQueueFlows::test_task_moves_through_custom_queues` is currently `xfail` and will pass once this is fixed. Remove the `xfail` marker when deploying.

## Priority

P2 — custom flows work for intermediate states but can't define custom terminal transitions. Not blocking current workflows since the default flow uses `provisional → done`.
