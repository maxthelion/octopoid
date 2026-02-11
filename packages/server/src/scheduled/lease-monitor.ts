/**
 * Scheduled job: Lease monitoring
 * Runs every minute to:
 * - Release expired task leases
 * - Mark stale orchestrators as offline
 */

import type { D1Database } from '@cloudflare/workers-types'
import { execute } from '../database'
import { getConfig } from '../config'

export async function runLeaseMonitor(db: D1Database): Promise<void> {
  console.log('Running lease monitor at:', new Date().toISOString())

  const config = getConfig()

  // 1. Release expired leases
  const releaseResult = await execute(
    db,
    `UPDATE tasks
     SET queue = 'incoming',
         claimed_by = NULL,
         orchestrator_id = NULL,
         lease_expires_at = NULL,
         updated_at = datetime('now')
     WHERE queue = 'claimed'
     AND lease_expires_at < datetime('now')`
  )

  if (releaseResult.meta.changes > 0) {
    console.log(`Released ${releaseResult.meta.changes} expired leases`)

    // Record history for each released task
    await execute(
      db,
      `INSERT INTO task_history (task_id, event, agent, details, timestamp)
       SELECT id, 'requeued', claimed_by, 'Lease expired', datetime('now')
       FROM tasks
       WHERE queue = 'incoming'
       AND claimed_by IS NOT NULL
       AND lease_expires_at IS NULL`
    )
  }

  // 2. Mark stale orchestrators as offline
  const staleThreshold = new Date(
    Date.now() - config.staleOrchestratorTimeoutSeconds * 1000
  ).toISOString()

  const staleResult = await execute(
    db,
    `UPDATE orchestrators
     SET status = 'offline'
     WHERE status = 'active'
     AND last_heartbeat < ?`,
    staleThreshold
  )

  if (staleResult.meta.changes > 0) {
    console.log(`Marked ${staleResult.meta.changes} orchestrators as offline`)
  }

  console.log('Lease monitor completed')
}
