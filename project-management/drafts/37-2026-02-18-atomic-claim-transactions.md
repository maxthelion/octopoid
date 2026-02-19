# Atomic Claim: Fix Orphaned Task State with Server-Side Transactions

**Status:** Idea
**Captured:** 2026-02-18

## Raw

> For how to solve this situation. Think of things being in transactions.

## Idea

Tasks are ending up in `claimed` queue with `claimed_by = NULL` — orphaned state. No agent owns them, no agent will pick them up (implementers claim from `incoming`, not `claimed`), and they sit there until a human notices and manually requeues them. Found 2 orphans today: TASK-b0a63d8b and TASK-451ec77d.

The root cause is that the server's claim endpoint performs **two separate SQL operations** without a transaction:

```typescript
// Step 1: executeTransition() — moves queue from 'incoming' to 'claimed'
const transitionResult = await executeTransition(db, task.id, transition, {...})

// Step 2: separate UPDATE — sets claimed_by and claimed_at
await execute(db, `UPDATE tasks SET claimed_by = ?, claimed_at = datetime('now') ...`, body.agent_name, task.id)
```

If step 1 succeeds but step 2 fails (timeout, error, client disconnect), the task is in `claimed` with no owner. The same non-transactional pattern exists in other endpoints:

- **Submit** (`/tasks/:id/submit`): `executeTransition` moves to provisional, then separate UPDATE for `commits_count`, `turns_used`, `submitted_at`
- **Reject** (`/tasks/:id/reject`): `executeTransition` moves to incoming, then separate UPDATE for `rejection_count`, `claimed_by = NULL`
- **Claim** (`/tasks/claim`): `executeTransition` moves to claimed, then separate UPDATE for `claimed_by`, `claimed_at`

Every endpoint with side effects has this two-step pattern. They should all be atomic.

## Fix: Merge Into Single SQL Statement

D1 supports transactions via `db.batch()` which runs multiple statements atomically. But the simpler fix is to merge the state change and metadata update into a single UPDATE:

### Option A: Fold claimed_by into executeTransition

Make `executeTransition` accept arbitrary field updates and include them in the same UPDATE:

```typescript
// executeTransition now handles everything in one UPDATE
const result = await execute(
  db,
  `UPDATE tasks
   SET queue = ?,
       version = ?,
       claimed_by = ?,
       claimed_at = datetime('now'),
       updated_at = datetime('now')
   WHERE id = ? AND version = ?`,
  transition.to, newVersion, agentName, taskId, task.version
)
```

Pro: One round-trip, fully atomic. Con: `executeTransition` becomes more complex.

### Option B: Use D1 batch()

Wrap existing operations in a batch:

```typescript
const statements = [
  db.prepare(`UPDATE tasks SET queue = ?, version = ? WHERE id = ? AND version = ?`)
    .bind(transition.to, newVersion, taskId, task.version),
  db.prepare(`UPDATE tasks SET claimed_by = ?, claimed_at = datetime('now') WHERE id = ?`)
    .bind(body.agent_name, task.id),
]
await db.batch(statements)
```

Pro: Keeps existing structure. Con: D1 batch semantics may differ from true transactions.

### Option C: Single UPDATE per endpoint (recommended)

Don't change `executeTransition`. Instead, merge the post-transition UPDATE into the transition itself by adding the fields to the same UPDATE statement. Each endpoint builds one UPDATE that does everything:

```typescript
// Claim endpoint — one atomic UPDATE
await execute(db,
  `UPDATE tasks
   SET queue = 'claimed',
       version = version + 1,
       claimed_by = ?,
       claimed_at = datetime('now'),
       lease_expires_at = ?,
       updated_at = datetime('now')
   WHERE id = ? AND queue = 'incoming' AND version = ?`,
  body.agent_name, leaseExpiry, task.id, task.version
)
```

This is the most robust: one SQL statement, fully atomic, with optimistic locking. The `WHERE queue = 'incoming' AND version = ?` acts as both a guard and a lock. If it changes 0 rows, the claim failed (race or wrong state).

## Affected Endpoints

| Endpoint | Step 1 (transition) | Step 2 (metadata) | Risk |
|----------|--------------------|--------------------|------|
| `POST /tasks/claim` | queue → claimed | claimed_by, claimed_at, lease | **Orphaned claimed task** (seen today) |
| `POST /tasks/:id/submit` | queue → provisional | commits_count, turns_used, submitted_at | Task in provisional with no metadata |
| `POST /tasks/:id/reject` | queue → incoming | rejection_count++, claimed_by = NULL | Task in incoming still showing old claimed_by |
| `POST /tasks/:id/accept` | queue → done | completed_at | Task done without completion timestamp |

## Context

Found during investigation of why TASK-b0a63d8b and TASK-451ec77d were stuck in `claimed` with `claimed_by = NULL`. Both created today, both orphaned within ~20 minutes. The scheduler never logged claiming them (task IDs absent from scheduler log), suggesting the claim may have partially succeeded on the server but the response never reached the client — or the client SDK hit an error after the server committed step 1.

This connects to draft #25 (fix PR metadata loss) — the submit endpoint has the same two-step pattern. Atomic submit would fix both issues.

## Open Questions

- Does D1 `batch()` provide true transactional atomicity (all-or-nothing), or just sequential execution?
- Should we also add a server-side cleanup job that detects orphans (queue=claimed, claimed_by=NULL, older than N minutes) and requeues them?
- Should the scheduler detect this on its side too — check for claimed tasks with no claimed_by and requeue them?

## Possible Next Steps

- Audit all server endpoints for the two-step pattern
- Refactor claim endpoint first (highest impact — causes orphans that block agents)
- Add orphan detection to scheduler housekeeping as a safety net
- Consider adding a server health check endpoint that reports orphaned tasks
