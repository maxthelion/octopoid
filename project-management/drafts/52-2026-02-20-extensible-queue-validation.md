# Extensible Queue Names With Runtime Validation

**Status:** Idea
**Captured:** 2026-02-20

## Raw

> Look at how we could generalize queue names so projects can define their own pipeline stages via flows without server changes. Specifically how we could ensure that it doesn't result in silly typos. Would the queues need to be registered with the server somehow? Would the flows? Where would the check happen that everything matched up?

## The Problem Today

Queue names are hardcoded in three TypeScript/Python type unions that must stay in sync manually:

| File | Type | Enforcement |
|------|------|-------------|
| `submodules/server/src/types/shared.ts` | `TaskQueue` union | Compile-time only |
| `packages/shared/src/task.ts` | `TaskQueue` union | Compile-time only (duplicate) |
| `orchestrator/config.py` | `Literal[...]` | Type hint only |

**There is zero runtime validation of queue names anywhere.** The D1 schema has no CHECK constraint. The PATCH endpoint accepts any string. The SDK passes values straight through. A typo like `"provisinoal"` silently reaches the database and the task disappears from all views.

Adding new queues (e.g. `human_review`, `sanity_approved`) currently requires editing all three type definitions, adding `TRANSITIONS` entries in `state-machine.ts`, and redeploying the server. This doesn't scale when different projects want different pipelines.

## Idea

Make queue names **project-defined via flows** but **validated at runtime** so typos are caught immediately rather than silently corrupting state.

## Where Validation Should Happen

### 1. Flow registration on the server

Flows define which queue names exist for a project. When an orchestrator registers (or on first poll), it should upload its flow definitions to the server:

```
POST /api/v1/flows
{
  "flow_name": "default",
  "states": ["incoming", "claimed", "provisional", "sanity_approved", "human_review", "done", "failed"],
  "transitions": [
    {"from": "incoming", "to": "claimed"},
    {"from": "claimed", "to": "provisional"},
    {"from": "provisional", "to": "sanity_approved"},
    ...
  ]
}
```

The server stores the valid states per flow. This is the **source of truth** for what queue names are legal.

### 2. Server-side validation on every queue write

Replace the compile-time `TaskQueue` union with a runtime check. Every endpoint that writes a `queue` value (PATCH, claim, submit, reject, accept) validates the value against the registered flow states:

```typescript
// Before: compile-time only
const body = (await c.req.json()) as UpdateTaskRequest

// After: runtime validation
const validQueues = await getRegisteredQueues(db, task.flow)
if (body.queue && !validQueues.includes(body.queue)) {
  return c.json({ error: `Invalid queue "${body.queue}". Valid queues: ${validQueues.join(', ')}` }, 400)
}
```

This is where typos get caught -- at the API boundary, with a clear error message.

### 3. Orchestrator-side validation at flow load time

`flow.py` already validates reachability. Extend it to also:
- Check that all states in the flow match the registered states on the server
- Warn on startup if the local flow is out of sync with the server

### 4. SDK-side validation (optional, defense in depth)

The Python SDK could cache the valid queue list from the server and validate locally before making API calls. This gives faster, offline-capable feedback but isn't strictly necessary if the server validates.

## Design Details

### What gets registered?

A new `flows` table on the server:

```sql
CREATE TABLE flows (
  name TEXT NOT NULL,
  cluster TEXT NOT NULL DEFAULT 'default',
  states TEXT NOT NULL,       -- JSON array: ["incoming", "claimed", ...]
  transitions TEXT NOT NULL,  -- JSON array of {from, to} pairs
  registered_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (name, cluster)
);
```

Each cluster can have different flows. The `tasks.flow` column (already exists, defaults to `"default"`) references the flow name. States are validated against the flow the task belongs to.

### Built-in states vs. flow-defined states

Some states are universal and shouldn't need registration:
- `incoming` -- every flow starts here
- `claimed` -- the server's lease/claim machinery depends on this
- `done`, `failed` -- terminal states with special handling

These could be a small set of **built-in states** that are always valid. Flow-defined states extend this set. The TRANSITIONS object in `state-machine.ts` could become dynamic, loaded from the `flows` table rather than hardcoded.

### What about the TypeScript `TaskQueue` type?

Replace the closed union with `string` at the type level, and move validation to runtime:

```typescript
// Before
export type TaskQueue = 'incoming' | 'claimed' | 'provisional' | ...

// After
export type TaskQueue = string  // validated at runtime against registered flows

// Built-in states that always exist
export const BUILT_IN_QUEUES = ['incoming', 'claimed', 'done', 'failed'] as const
```

### What about the Python `TaskQueue` Literal?

Same approach -- relax to `str` and validate at the API boundary:

```python
# Before
TaskQueue = Literal["incoming", "claimed", ...]

# After
TaskQueue = str  # validated by server
BUILT_IN_QUEUES = {"incoming", "claimed", "done", "failed"}
```

### Flow-to-server sync

The orchestrator already registers itself via `POST /api/v1/orchestrators/register`. Extend this to also upload the local flow definitions:

```python
def _register_orchestrator():
    sdk.register(orchestrator_id, ...)

    # Sync flows
    for flow_file in flows_dir.glob("*.yaml"):
        flow = load_flow(flow_file.stem)
        sdk.flows.register(
            name=flow.name,
            states=flow.all_states(),
            transitions=[(t.from_state, t.to_state) for t in flow.transitions],
        )
```

This happens once every 5 minutes (the existing registration interval). If flows change, they're re-uploaded on the next tick.

### Dynamic TRANSITIONS

The hardcoded `TRANSITIONS` object in `state-machine.ts` currently defines 8 transitions with guards and side effects. These need to become a combination of:
- **Built-in transitions** (claim, submit, accept, reject, requeue) with their guards
- **Flow-defined transitions** loaded from the `flows` table

The built-in transitions handle the mechanics (lease management, claim atomicity). Flow-defined transitions handle the pipeline stages (what comes after provisional, etc.).

## Validation Chain Summary

```
Flow YAML authored locally
         |
         v
flow.py validate() -- checks reachability, agent refs
         |
         v
Orchestrator registers flow with server
         |
         v
Server stores valid states in flows table
         |
         v
Every API write checks queue value against flows.states
         |
         v
Typo → immediate 400 error with valid queue list
```

## Open Questions

- Should flows be per-cluster or global? (Per-cluster lets different environments have different pipelines.)
- Should the server enforce transition legality (can only move from state A to state B per the flow) or just validate that the queue name is legal? Full transition enforcement is more correct but significantly more complex.
- How do we handle flow migrations? (Adding a new state is fine, but removing or renaming a state while tasks are in it needs care.)
- Should the dashboard learn about flow states dynamically? (e.g. auto-generate kanban columns from the flow definition rather than hardcoding Incoming/In Progress/In Review.)

## Possible Next Steps

1. Add `flows` table to server (migration)
2. Add `POST /api/v1/flows` endpoint for registration
3. Add runtime queue validation to PATCH/claim/submit/reject/accept endpoints
4. Add flow sync to orchestrator registration
5. Remove hardcoded `TaskQueue` unions, replace with runtime validation
6. (Later) Make TRANSITIONS dynamic from flow definitions
7. (Later) Dashboard auto-generates columns from flow states
