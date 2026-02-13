# Debug Endpoints - Server Implementation Guide

This document describes the server-side implementation required for the debug/observability endpoints added in GH-9.

## Overview

The debug endpoints provide visibility into task states, queue health, and agent activity. The client-side types, SDK methods, and CLI commands are already implemented. This guide describes what needs to be implemented on the server.

## Endpoints to Implement

### 1. Task Debug Info

**Endpoint:** `GET /api/v1/tasks/:id/debug`

**Purpose:** Debug information for a specific task

**Response Type:** `TaskDebugInfo` (see `packages/shared/src/debug.ts`)

**Implementation:**

```typescript
{
  task_id: string
  state: TaskQueue  // Current queue
  lease_expires_in: string | null  // e.g., "14m 32s" (null if not claimed)
  lease_expires_at: string | null  // ISO timestamp (null if not claimed)
  blocking: {
    is_blocked: boolean  // true if blocked_by is set
    blocked_by: string | null  // Task ID
    blocks: string[]  // Task IDs where blocked_by = this task's ID
  }
  burnout: {
    is_burned_out: boolean  // Check against configured threshold
    turns_used: number
    commits_count: number
    threshold: number  // From server config (default: 80)
  }
  gatekeeper: {
    review_round: number
    max_rounds: number  // From server config (default: 3)
    rejection_count: number
  }
  attempts: {
    attempt_count: number
    last_claimed_at: string | null  // ISO timestamp
    last_submitted_at: string | null  // ISO timestamp
  }
}
```

**SQL Queries:**

```sql
-- Get task data
SELECT * FROM tasks WHERE id = ?

-- Get tasks blocked by this task
SELECT id FROM tasks WHERE blocked_by = ?
```

**Logic:**
- Calculate `lease_expires_in` from `lease_expires_at - now()`
- `is_burned_out = turns_used >= threshold OR commits_count >= threshold`
- `is_blocked = blocked_by IS NOT NULL`

---

### 2. Queue Debug Info

**Endpoint:** `GET /api/v1/debug/queues`

**Purpose:** Overview of all queues with stats

**Response Type:** `QueueDebugInfo` (see `packages/shared/src/debug.ts`)

**Implementation:**

```typescript
{
  queues: {
    [queue]: {
      count: number
      oldest_task: {
        id: string
        age: string  // Human-readable, e.g., "2h 14m"
        created_at: string  // ISO timestamp
      } | null
    }
  }
  claimed: {
    count: number
    tasks: [{
      id: string
      claimed_by: string
      orchestrator_id: string
      claimed_for: string  // Duration since claimed_at
      lease_expires_in: string  // Duration until lease_expires_at
      lease_expires_at: string  // ISO timestamp
    }]
  }
}
```

**SQL Queries:**

```sql
-- Count tasks per queue
SELECT queue, COUNT(*) as count FROM tasks GROUP BY queue

-- Get oldest task per queue
SELECT id, created_at FROM tasks
WHERE queue = ?
ORDER BY created_at ASC
LIMIT 1

-- Get all claimed tasks
SELECT id, claimed_by, orchestrator_id, claimed_at, lease_expires_at
FROM tasks
WHERE queue = 'claimed'
ORDER BY claimed_at ASC
```

**Helper Function:**

```typescript
function formatDuration(start: Date, end: Date): string {
  const diff = Math.abs(end.getTime() - start.getTime())
  const hours = Math.floor(diff / 3600000)
  const minutes = Math.floor((diff % 3600000) / 60000)
  const seconds = Math.floor((diff % 60000) / 1000)

  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${seconds}s`
  return `${seconds}s`
}
```

---

### 3. Agent Debug Info

**Endpoint:** `GET /api/v1/debug/agents`

**Purpose:** Agent activity and orchestrator health

**Response Type:** `AgentDebugInfo` (see `packages/shared/src/debug.ts`)

**Implementation:**

```typescript
{
  orchestrators: [{
    orchestrator_id: string
    cluster: string
    machine_id: string
    status: 'active' | 'idle' | 'offline'  // Based on heartbeat age
    last_heartbeat_at: string | null
    heartbeat_age: string | null  // e.g., "3m 45s ago"
    current_tasks: number  // Count of claimed tasks
    total_completed: number  // From task_history
    total_failed: number  // From task_history
  }]
  agents: [{
    orchestrator_id: string
    agent_name: string
    role: string
    current_task: {
      id: string
      claimed_at: string
      lease_expires_at: string
    } | null
    stats: {
      tasks_claimed: number
      tasks_completed: number
      tasks_failed: number
      success_rate: number  // 0.0 to 1.0
    }
  }]
  summary: {
    total_orchestrators: number
    active_orchestrators: number
    total_agents: number
    total_claimed_tasks: number
  }
}
```

**SQL Queries:**

```sql
-- Get orchestrators
SELECT * FROM orchestrators

-- Get current tasks per orchestrator
SELECT COUNT(*) FROM tasks
WHERE queue = 'claimed' AND orchestrator_id = ?

-- Get completion stats from task_history
SELECT
  COUNT(*) as total,
  SUM(CASE WHEN event = 'completed' THEN 1 ELSE 0 END) as completed,
  SUM(CASE WHEN event = 'failed' THEN 1 ELSE 0 END) as failed
FROM task_history
WHERE orchestrator_id = ?

-- Get claimed tasks per agent (from claimed_by field)
SELECT id, claimed_at, lease_expires_at
FROM tasks
WHERE claimed_by = ? AND queue = 'claimed'
```

**Status Logic:**
- `active`: last_heartbeat_at within 5 minutes
- `idle`: last_heartbeat_at between 5-15 minutes
- `offline`: last_heartbeat_at > 15 minutes or null

---

### 4. System Status

**Endpoint:** `GET /api/v1/debug/status`

**Purpose:** Comprehensive system overview

**Response Type:** `SystemStatusInfo` (see `packages/shared/src/debug.ts`)

**Implementation:**

Combines data from the above three endpoints plus:

```typescript
{
  timestamp: string  // ISO timestamp of this snapshot
  queues: QueueDebugInfo  // From debug/queues
  agents: AgentDebugInfo  // From debug/agents
  health: {
    oldest_incoming_task: {
      id: string
      age: string
      created_at: string
    } | null
    stuck_tasks: [{
      id: string
      queue: TaskQueue
      issue: string  // Why it's stuck
      claimed_at: string | null
      lease_expires_at: string | null
    }]
    zombie_claims: [{  // Claimed tasks with expired leases
      id: string
      claimed_by: string
      orchestrator_id: string
      lease_expired: boolean
      lease_expires_at: string
    }]
  }
  metrics: {
    avg_time_to_claim: string | null  // From incoming -> claimed
    avg_time_to_complete: string | null  // From claimed -> done
    tasks_created_24h: number
    tasks_completed_24h: number
    tasks_failed_24h: number
  }
}
```

**Stuck Task Detection:**

```sql
-- Tasks blocked by non-existent tasks
SELECT id, queue, blocked_by
FROM tasks
WHERE blocked_by IS NOT NULL
  AND blocked_by NOT IN (SELECT id FROM tasks)

-- Tasks with expired leases (zombies)
SELECT id, claimed_by, orchestrator_id, queue, lease_expires_at
FROM tasks
WHERE queue = 'claimed'
  AND lease_expires_at < datetime('now')

-- Tasks in provisional for too long (> 1 hour)
SELECT id, queue, submitted_at
FROM tasks
WHERE queue = 'provisional'
  AND submitted_at < datetime('now', '-1 hour')
```

**Metrics Queries:**

```sql
-- Avg time to claim (incoming -> claimed)
SELECT AVG(
  julianday(claimed_at) - julianday(created_at)
) * 24 * 60 * 60 as avg_seconds
FROM tasks
WHERE claimed_at IS NOT NULL
  AND created_at > datetime('now', '-7 days')

-- Avg time to complete (claimed -> done)
SELECT AVG(
  julianday(completed_at) - julianday(claimed_at)
) * 24 * 60 * 60 as avg_seconds
FROM tasks
WHERE completed_at IS NOT NULL
  AND claimed_at IS NOT NULL
  AND created_at > datetime('now', '-7 days')

-- 24h task counts
SELECT COUNT(*) FROM tasks
WHERE created_at > datetime('now', '-1 day')

SELECT COUNT(*) FROM tasks
WHERE queue = 'done'
  AND completed_at > datetime('now', '-1 day')

SELECT COUNT(*) FROM tasks
WHERE queue = 'failed'
  AND updated_at > datetime('now', '-1 day')
```

---

## Configuration

The following server configuration values should be added:

```typescript
// config/debug.ts or similar
export const DEBUG_CONFIG = {
  burnout_threshold: 80,  // Max turns/commits before burnout
  gatekeeper_max_rounds: 3,  // Max review rounds
  heartbeat_active_threshold: 300,  // 5 minutes
  heartbeat_idle_threshold: 900,  // 15 minutes
  provisional_timeout: 3600,  // 1 hour
}
```

---

## Testing

1. Create test tasks in various states (incoming, claimed, blocked, burned out)
2. Register test orchestrators with different heartbeat ages
3. Test each endpoint individually
4. Test the comprehensive status endpoint
5. Verify human-readable duration formatting
6. Test edge cases (no tasks, no orchestrators, expired leases)

---

## Integration with Client

The client code is already implemented:

- **Types:** `packages/shared/src/debug.ts`
- **SDK:** `packages/python-sdk/octopoid_sdk/client.py` (DebugAPI class)
- **CLI:** `orchestrator/cli.py` (cmd_debug_* functions)

Once the server endpoints are deployed, the CLI commands will work immediately:

```bash
octopoid debug-task <id>
octopoid debug-queues
octopoid debug-agents
octopoid debug-status
```

---

## Server Repository

The server implementation should be added to:
**https://github.com/maxthelion/octopoid-server**

Suggested files:
- `src/routes/debug.ts` - Debug route handlers
- `src/services/debug.ts` - Debug logic and queries
- `src/utils/duration.ts` - Duration formatting helper

---

## Migration Notes

No database migrations are required. All debug endpoints use existing task and orchestrator data.

---

## Performance Considerations

- Cache queue counts for 10-30 seconds (high read frequency)
- Limit claimed tasks list to recent 100 tasks
- Add indexes if needed:
  - `CREATE INDEX idx_tasks_blocked_by ON tasks(blocked_by)`
  - `CREATE INDEX idx_tasks_queue_created ON tasks(queue, created_at)`
  - `CREATE INDEX idx_tasks_claimed_lease ON tasks(queue, lease_expires_at)`

---

## Next Steps

1. Implement endpoints in octopoid-server repository
2. Deploy to Cloudflare Workers
3. Test with production Octopoid installation
4. Monitor performance and add caching as needed
