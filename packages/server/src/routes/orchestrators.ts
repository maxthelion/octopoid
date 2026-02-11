/**
 * Orchestrator API routes
 */

import { Hono } from 'hono'
import type {
  Orchestrator,
  RegisterOrchestratorRequest,
  RegisterOrchestratorResponse,
  HeartbeatRequest,
  HeartbeatResponse,
  OrchestratorListResponse,
} from '@octopoid/shared'
import type { Env } from '../index'
import { query, queryOne, execute } from '../database'

export const orchestratorsRoute = new Hono<{ Bindings: Env }>()

/**
 * Register orchestrator
 * POST /api/v1/orchestrators/register
 */
orchestratorsRoute.post('/register', async (c) => {
  const db = c.env.DB
  const body = (await c.req.json()) as RegisterOrchestratorRequest

  // Validate required fields
  if (!body.cluster || !body.machine_id || !body.repo_url) {
    return c.json(
      { error: 'Missing required fields: cluster, machine_id, repo_url' },
      400
    )
  }

  // Generate orchestrator ID
  const orchestratorId = `${body.cluster}-${body.machine_id}`

  // Check if orchestrator already exists
  const existing = await queryOne<Orchestrator>(
    db,
    'SELECT * FROM orchestrators WHERE id = ?',
    orchestratorId
  )

  const now = new Date().toISOString()

  if (existing) {
    // Update existing orchestrator (re-registration)
    await execute(
      db,
      `UPDATE orchestrators
       SET hostname = ?,
           repo_url = ?,
           last_heartbeat = ?,
           status = 'active',
           version = ?,
           capabilities = ?,
           updated_at = datetime('now')
       WHERE id = ?`,
      body.hostname || null,
      body.repo_url,
      now,
      body.version || null,
      body.capabilities ? JSON.stringify(body.capabilities) : null,
      orchestratorId
    )
  } else {
    // Insert new orchestrator
    await execute(
      db,
      `INSERT INTO orchestrators (
        id, cluster, machine_id, hostname, repo_url,
        registered_at, last_heartbeat, status, version, capabilities
      ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)`,
      orchestratorId,
      body.cluster,
      body.machine_id,
      body.hostname || null,
      body.repo_url,
      now,
      now,
      body.version || null,
      body.capabilities ? JSON.stringify(body.capabilities) : null
    )
  }

  const response: RegisterOrchestratorResponse = {
    orchestrator_id: orchestratorId,
    registered_at: now,
    status: 'active',
  }

  return c.json(response, existing ? 200 : 201)
})

/**
 * Send heartbeat
 * POST /api/v1/orchestrators/:id/heartbeat
 */
orchestratorsRoute.post('/:id/heartbeat', async (c) => {
  const db = c.env.DB
  const orchestratorId = c.req.param('id')
  const body = (await c.req.json()) as HeartbeatRequest

  // Check if orchestrator exists
  const orchestrator = await queryOne<Orchestrator>(
    db,
    'SELECT * FROM orchestrators WHERE id = ?',
    orchestratorId
  )

  if (!orchestrator) {
    return c.json({ error: 'Orchestrator not found' }, 404)
  }

  // Update last_heartbeat
  const now = body.timestamp || new Date().toISOString()
  await execute(
    db,
    `UPDATE orchestrators
     SET last_heartbeat = ?,
         status = 'active'
     WHERE id = ?`,
    now,
    orchestratorId
  )

  const response: HeartbeatResponse = {
    success: true,
    last_heartbeat: now,
  }

  return c.json(response)
})

/**
 * List orchestrators
 * GET /api/v1/orchestrators?cluster=prod&status=active
 */
orchestratorsRoute.get('/', async (c) => {
  const db = c.env.DB

  // Parse query parameters
  const cluster = c.req.query('cluster')
  const statusParam = c.req.query('status')

  // Build WHERE clause
  const conditions: string[] = []
  const params: unknown[] = []

  if (cluster) {
    conditions.push('cluster = ?')
    params.push(cluster)
  }

  if (statusParam) {
    const statuses = statusParam.split(',')
    conditions.push(`status IN (${statuses.map(() => '?').join(',')})`)
    params.push(...statuses)
  }

  const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : ''

  // Get orchestrators
  const orchestrators = await query<Orchestrator>(
    db,
    `SELECT * FROM orchestrators ${whereClause}
     ORDER BY last_heartbeat DESC`,
    ...params
  )

  const response: OrchestratorListResponse = {
    orchestrators,
    total: orchestrators.length,
  }

  return c.json(response)
})

/**
 * Get orchestrator by ID
 * GET /api/v1/orchestrators/:id
 */
orchestratorsRoute.get('/:id', async (c) => {
  const db = c.env.DB
  const orchestratorId = c.req.param('id')

  const orchestrator = await queryOne<Orchestrator>(
    db,
    'SELECT * FROM orchestrators WHERE id = ?',
    orchestratorId
  )

  if (!orchestrator) {
    return c.json({ error: 'Orchestrator not found' }, 404)
  }

  return c.json(orchestrator)
})

/**
 * Update orchestrator status (admin)
 * PATCH /api/v1/orchestrators/:id
 */
orchestratorsRoute.patch('/:id', async (c) => {
  const db = c.env.DB
  const orchestratorId = c.req.param('id')
  const body = await c.req.json()

  // Only allow updating status
  if (!body.status) {
    return c.json({ error: 'Missing field: status' }, 400)
  }

  const result = await execute(
    db,
    `UPDATE orchestrators
     SET status = ?
     WHERE id = ?`,
    body.status,
    orchestratorId
  )

  if (result.meta.changes === 0) {
    return c.json({ error: 'Orchestrator not found' }, 404)
  }

  const orchestrator = await queryOne<Orchestrator>(
    db,
    'SELECT * FROM orchestrators WHERE id = ?',
    orchestratorId
  )

  return c.json(orchestrator)
})
