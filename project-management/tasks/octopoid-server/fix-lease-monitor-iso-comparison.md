# Fix lease-monitor.ts: ISO timestamp comparison and history recording bugs

## Context

The server's scheduled lease monitor (`src/scheduled/lease-monitor.ts`) has two bugs that prevent it from working correctly.

## Bug 1: ISO timestamp comparison never fires for same-day leases

### Problem

The monitor uses this SQL to release expired leases:

```sql
UPDATE tasks
SET queue = 'incoming',
    claimed_by = NULL,
    ...
WHERE queue = 'claimed'
AND lease_expires_at < datetime('now')
```

`datetime('now')` in SQLite returns `"YYYY-MM-DD HH:MM:SS"` (space-separated). Task leases are stored as ISO 8601 strings: `"YYYY-MM-DDTHH:MM:SS.mmmZ"` (T-separated). SQLite compares these as strings:

- `'T'` (0x54) > `' '` (0x20)
- So `"2026-03-02T10:00:00.000Z"` > `"2026-03-02 15:00:00"` — the condition is always FALSE

**Result:** Same-day expired leases are NEVER released by the server monitor. A lease from hours ago still passes the condition as "not expired". Only leases from a previous calendar day would expire server-side.

The orchestrator's Python scheduler handles lease expiry as a fallback (via `check_and_requeue_expired_leases`), but the server-side monitor should also work correctly.

### Fix

Use SQLite's `datetime()` function to normalize both sides before comparing:

```sql
WHERE queue = 'claimed'
AND datetime(lease_expires_at) < datetime('now')
```

`datetime(value)` in SQLite correctly parses ISO 8601 strings (including `T`-separated and `Z` suffix), converting them to SQLite's native `"YYYY-MM-DD HH:MM:SS"` format for comparison. Both sides are then in the same format.

## Bug 2: History recording runs after claimed_by is cleared

### Problem

The history INSERT runs AFTER the UPDATE has already set `claimed_by = NULL`:

```javascript
// 1. This sets claimed_by = NULL on all expired tasks
const releaseResult = await execute(db, `
  UPDATE tasks SET ... claimed_by = NULL ...
  WHERE queue = 'claimed' AND lease_expires_at < datetime('now')
`)

// 2. This tries to find tasks WHERE claimed_by IS NOT NULL — but they're all NULL now!
await execute(db, `
  INSERT INTO task_history (task_id, event, agent, details, timestamp)
  SELECT id, 'requeued', claimed_by, 'Lease expired', datetime('now')
  FROM tasks
  WHERE queue = 'incoming'
  AND claimed_by IS NOT NULL   ← never matches, claimed_by was just cleared
  AND lease_expires_at IS NULL
`)
```

**Result:** The `agent` column in `task_history` is always NULL for lease-expiry requeue events. The agent name is permanently lost.

### Fix

Record history BEFORE clearing `claimed_by`. Use a CTE or two-step approach:

**Option A: CTE (single statement)**
```sql
WITH expired AS (
  SELECT id, claimed_by
  FROM tasks
  WHERE queue = 'claimed'
  AND datetime(lease_expires_at) < datetime('now')
)
UPDATE tasks
SET queue = 'incoming', claimed_by = NULL, orchestrator_id = NULL,
    lease_expires_at = NULL, updated_at = datetime('now')
WHERE id IN (SELECT id FROM expired)
```

Then insert history using the `expired` CTE data. However, D1 may not support CTEs in this form. A simpler alternative:

**Option B: SELECT first, then UPDATE**
```javascript
// 1. Find expired tasks (capture claimed_by before clearing)
const expiredTasks = await query(db, `
  SELECT id, claimed_by FROM tasks
  WHERE queue = 'claimed'
  AND datetime(lease_expires_at) < datetime('now')
`)

// 2. Release leases
await execute(db, `
  UPDATE tasks SET queue = 'incoming', claimed_by = NULL, orchestrator_id = NULL,
      lease_expires_at = NULL, updated_at = datetime('now')
  WHERE queue = 'claimed'
  AND datetime(lease_expires_at) < datetime('now')
`)

// 3. Record history with captured agent names
for (const task of expiredTasks) {
  await execute(db,
    `INSERT INTO task_history (task_id, event, agent, details, timestamp)
     VALUES (?, 'requeued', ?, 'Lease expired', datetime('now'))`,
    task.id, task.claimed_by
  )
}
```

## Files to Change

- `src/scheduled/lease-monitor.ts`

## Acceptance Criteria

- [ ] `datetime(lease_expires_at) < datetime('now')` is used for comparisons
- [ ] Expired leases are released server-side without requiring the Python orchestrator fallback
- [ ] `task_history` records the correct `agent` (claimed_by value) for lease-expiry requeue events
- [ ] Add a test: claim a task with a 1-second lease, wait 2 seconds, confirm the monitor releases it
