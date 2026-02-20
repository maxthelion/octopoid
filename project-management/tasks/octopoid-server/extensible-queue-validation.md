# Extensible queue names with runtime validation

Replace the hardcoded `TaskQueue` TypeScript union with runtime validation against flow-registered states. This lets projects define their own pipeline stages (e.g. `human_review`, `sanity_approved`) via flow YAML without server redeployment.

Related drafts: #52 (extensible queue validation), #51 (rejection feedback loop)
Related task: `project-management/tasks/add-human-review-queues.md` (superseded by this)

## Background

Queue names are currently a closed union in `src/types/shared.ts` (and duplicated in `packages/shared/src/task.ts`). Adding a new queue requires editing both files, adding `TRANSITIONS` entries in `state-machine.ts`, and redeploying. There is no runtime validation -- a typo in any API call silently reaches the database.

## Work items

### 1. Add `flows` table (migration)

```sql
CREATE TABLE flows (
  name TEXT NOT NULL,
  cluster TEXT NOT NULL DEFAULT 'default',
  states TEXT NOT NULL,         -- JSON array: ["incoming", "claimed", "provisional", ...]
  transitions TEXT NOT NULL,    -- JSON array: [{"from": "incoming", "to": "claimed"}, ...]
  registered_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (name, cluster)
);
```

### 2. Add flow registration endpoint

```
PUT /api/v1/flows/:name
{
  "cluster": "default",
  "states": ["incoming", "claimed", "provisional", "sanity_approved", "human_review", "done", "failed"],
  "transitions": [
    {"from": "incoming", "to": "claimed"},
    {"from": "claimed", "to": "provisional"},
    {"from": "provisional", "to": "sanity_approved"},
    {"from": "sanity_approved", "to": "human_review"},
    {"from": "human_review", "to": "done"}
  ]
}
```

Use PUT (upsert) so the orchestrator can re-register on every startup without worrying about duplicates. Validate that `states` includes the built-in states (`incoming`, `claimed`, `done`, `failed`).

Also add:
- `GET /api/v1/flows` -- list all registered flows
- `GET /api/v1/flows/:name` -- get a specific flow's states and transitions

### 3. Add runtime queue validation

Create a `validateQueue` helper:

```typescript
const BUILT_IN_QUEUES = new Set(['incoming', 'claimed', 'done', 'failed'])

async function validateQueue(db: D1Database, queue: string, flowName: string = 'default'): Promise<string | null> {
  if (BUILT_IN_QUEUES.has(queue)) return null  // always valid

  const flow = await queryOne(db, 'SELECT states FROM flows WHERE name = ?', flowName)
  if (!flow) return null  // no flow registered, allow anything (backwards compat)

  const validStates: string[] = JSON.parse(flow.states)
  if (validStates.includes(queue)) return null  // valid

  return `Invalid queue "${queue}". Valid queues for flow "${flowName}": ${validStates.join(', ')}`
}
```

Add this check to every endpoint that writes a `queue` value:

| Endpoint | Where to add |
|----------|-------------|
| `PATCH /api/v1/tasks/:id` | Before building the UPDATE query, if `body.queue` is set |
| `POST /api/v1/tasks` | Before INSERT, validate `body.queue` |
| `POST /api/v1/tasks/claim` | Validate `body.queue` if provided |
| `POST /api/v1/tasks/:id/submit` | The target queue is hardcoded to `provisional` -- make it flow-aware later |
| `POST /api/v1/tasks/:id/reject` | The target queue comes from the flow's `on_fail` -- validate it |

Return 400 with the error message from `validateQueue` if validation fails.

### 4. Relax the TypeScript `TaskQueue` type

```typescript
// Before
export type TaskQueue =
  | 'incoming' | 'claimed' | 'provisional' | 'done' | 'failed'
  | 'rejected' | 'escalated' | 'recycled' | 'breakdown'
  | 'needs_continuation' | 'backlog' | 'blocked'

// After
export type TaskQueue = string  // validated at runtime against registered flows

export const BUILT_IN_QUEUES = ['incoming', 'claimed', 'done', 'failed'] as const
export type BuiltInQueue = typeof BUILT_IN_QUEUES[number]
```

Do the same in `packages/shared/src/task.ts`.

### 5. Backwards compatibility

If no flow is registered for a task's flow name, skip validation (allow any queue value). This means existing deployments without flow registration continue working. The validation only activates once a flow is registered.

Include the current default queue names in the `BUILT_IN_QUEUES` set OR auto-register a default flow on first deployment:

```typescript
const LEGACY_QUEUES = [
  'incoming', 'claimed', 'provisional', 'done', 'failed',
  'rejected', 'escalated', 'recycled', 'breakdown',
  'needs_continuation', 'backlog', 'blocked',
]
```

### 6. Include valid queues in poll response

Extend the `GET /api/v1/scheduler/poll` response to include the registered flow states:

```json
{
  "queue_counts": {...},
  "provisional_tasks": [...],
  "orchestrator_registered": true,
  "flows": {
    "default": {
      "states": ["incoming", "claimed", "provisional", "done", "failed"]
    }
  }
}
```

This lets the orchestrator cache valid queue names locally for fast validation.

## Acceptance criteria

- [ ] `flows` table exists with `name`, `cluster`, `states`, `transitions` columns
- [ ] `PUT /api/v1/flows/:name` registers/updates a flow (upsert)
- [ ] `GET /api/v1/flows` and `GET /api/v1/flows/:name` return registered flows
- [ ] `PATCH /api/v1/tasks/:id` returns 400 with clear error message when `queue` value is not in the registered flow's states
- [ ] `POST /api/v1/tasks` validates `queue` against registered flow states
- [ ] Typo like `"provisinoal"` returns: `Invalid queue "provisinoal". Valid queues for flow "default": incoming, claimed, provisional, done, failed`
- [ ] No flow registered -> validation skipped (backwards compatible)
- [ ] `TaskQueue` type is `string` (not a closed union)
- [ ] Built-in queues (`incoming`, `claimed`, `done`, `failed`) are always valid regardless of flow registration
- [ ] Existing tests still pass

## Not in scope (follow-up work)

- Dynamic TRANSITIONS (making the state machine flow-aware rather than hardcoded)
- Orchestrator-side flow sync (uploading flows during registration)
- Dashboard auto-generating columns from flow states
- Python `TaskQueue` Literal relaxation (orchestrator-side, separate task)
