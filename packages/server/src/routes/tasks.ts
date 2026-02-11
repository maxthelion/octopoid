/**
 * Task API routes
 */

import { Hono } from 'hono'
import type {
  Task,
  CreateTaskRequest,
  UpdateTaskRequest,
  ClaimTaskRequest,
  SubmitTaskRequest,
  AcceptTaskRequest,
  RejectTaskRequest,
  TaskListResponse,
} from '@octopoid/shared'
import type { Env } from '../index'
import { query, queryOne, execute } from '../database'
import { executeTransition, TRANSITIONS } from '../state-machine'
import { getConfig } from '../config'

export const tasksRoute = new Hono<{ Bindings: Env }>()

/**
 * List tasks with filters
 * GET /api/v1/tasks?queue=incoming&priority=P1&role=implement&limit=50&offset=0
 */
tasksRoute.get('/', async (c) => {
  const db = c.env.DB
  const config = getConfig()

  // Parse query parameters
  const queueParam = c.req.query('queue')
  const priorityParam = c.req.query('priority')
  const roleParam = c.req.query('role')
  const claimedBy = c.req.query('claimed_by')
  const projectId = c.req.query('project_id')
  const limit = Math.min(
    parseInt(c.req.query('limit') || String(config.defaultPageSize)),
    config.maxPageSize
  )
  const offset = parseInt(c.req.query('offset') || '0')

  // Build WHERE clause
  const conditions: string[] = []
  const params: unknown[] = []

  if (queueParam) {
    const queues = queueParam.split(',')
    conditions.push(`queue IN (${queues.map(() => '?').join(',')})`)
    params.push(...queues)
  }

  if (priorityParam) {
    const priorities = priorityParam.split(',')
    conditions.push(`priority IN (${priorities.map(() => '?').join(',')})`)
    params.push(...priorities)
  }

  if (roleParam) {
    const roles = roleParam.split(',')
    conditions.push(`role IN (${roles.map(() => '?').join(',')})`)
    params.push(...roles)
  }

  if (claimedBy) {
    conditions.push('claimed_by = ?')
    params.push(claimedBy)
  }

  if (projectId) {
    conditions.push('project_id = ?')
    params.push(projectId)
  }

  const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : ''

  // Get total count
  const countResult = await queryOne<{ count: number }>(
    db,
    `SELECT COUNT(*) as count FROM tasks ${whereClause}`,
    ...params
  )
  const total = countResult?.count || 0

  // Get tasks
  const tasks = await query<Task>(
    db,
    `SELECT * FROM tasks ${whereClause}
     ORDER BY priority ASC, created_at ASC
     LIMIT ? OFFSET ?`,
    ...params,
    limit,
    offset
  )

  const response: TaskListResponse = {
    tasks,
    total,
    offset,
    limit,
  }

  return c.json(response)
})

/**
 * Get task by ID
 * GET /api/v1/tasks/:id
 */
tasksRoute.get('/:id', async (c) => {
  const db = c.env.DB
  const taskId = c.req.param('id')

  const task = await queryOne<Task>(db, 'SELECT * FROM tasks WHERE id = ?', taskId)

  if (!task) {
    return c.json({ error: 'Task not found', task_id: taskId }, 404)
  }

  return c.json(task)
})

/**
 * Create task
 * POST /api/v1/tasks
 */
tasksRoute.post('/', async (c) => {
  const db = c.env.DB
  const body = (await c.req.json()) as CreateTaskRequest

  // Validate required fields
  if (!body.id || !body.file_path) {
    return c.json(
      { error: 'Missing required fields: id, file_path' },
      400
    )
  }

  // Insert task
  const result = await execute(
    db,
    `INSERT INTO tasks (
      id, file_path, queue, priority, complexity, role, branch,
      blocked_by, project_id, auto_accept, created_at, updated_at, version
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), 1)`,
    body.id,
    body.file_path,
    body.queue || 'incoming',
    body.priority || 'P2',
    body.complexity || null,
    body.role || null,
    body.branch || 'main',
    body.blocked_by || null,
    body.project_id || null,
    body.auto_accept || false
  )

  if (!result.success) {
    return c.json({ error: 'Failed to create task' }, 500)
  }

  // Return created task
  const task = await queryOne<Task>(db, 'SELECT * FROM tasks WHERE id = ?', body.id)

  return c.json(task, 201)
})

/**
 * Update task
 * PATCH /api/v1/tasks/:id
 */
tasksRoute.patch('/:id', async (c) => {
  const db = c.env.DB
  const taskId = c.req.param('id')
  const body = (await c.req.json()) as UpdateTaskRequest

  // Build SET clause dynamically
  const updates: string[] = []
  const params: unknown[] = []

  const fields = [
    'queue',
    'priority',
    'complexity',
    'role',
    'branch',
    'blocked_by',
    'claimed_by',
    'claimed_at',
    'commits_count',
    'turns_used',
    'attempt_count',
    'has_plan',
    'plan_id',
    'project_id',
    'auto_accept',
    'rejection_count',
    'pr_number',
    'pr_url',
    'checks',
    'check_results',
    'needs_rebase',
    'last_rebase_attempt_at',
    'staging_url',
    'submitted_at',
    'completed_at',
  ]

  for (const field of fields) {
    if (field in body) {
      updates.push(`${field} = ?`)
      params.push((body as any)[field])
    }
  }

  if (updates.length === 0) {
    return c.json({ error: 'No fields to update' }, 400)
  }

  updates.push("updated_at = datetime('now')")

  const sql = `UPDATE tasks SET ${updates.join(', ')} WHERE id = ?`
  params.push(taskId)

  const result = await execute(db, sql, ...params)

  if (result.meta.changes === 0) {
    return c.json({ error: 'Task not found' }, 404)
  }

  const task = await queryOne<Task>(db, 'SELECT * FROM tasks WHERE id = ?', taskId)

  return c.json(task)
})

/**
 * Claim task (atomic with lease)
 * POST /api/v1/tasks/claim
 */
tasksRoute.post('/claim', async (c) => {
  const db = c.env.DB
  const body = (await c.req.json()) as ClaimTaskRequest
  const config = getConfig()

  // Validate required fields
  if (!body.orchestrator_id || !body.agent_name) {
    return c.json(
      { error: 'Missing required fields: orchestrator_id, agent_name' },
      400
    )
  }

  // Build WHERE clause for role filter
  let roleCondition = ''
  const params: unknown[] = []

  if (body.role_filter) {
    const roles = Array.isArray(body.role_filter)
      ? body.role_filter
      : [body.role_filter]
    roleCondition = `AND role IN (${roles.map(() => '?').join(',')})`
    params.push(...roles)
  }

  // Find available task (no blocked_by, in incoming queue)
  const task = await queryOne<Task>(
    db,
    `SELECT * FROM tasks
     WHERE queue = 'incoming'
     AND (blocked_by IS NULL OR blocked_by = '')
     ${roleCondition}
     ORDER BY priority ASC, created_at ASC
     LIMIT 1`,
    ...params
  )

  if (!task) {
    return c.json({ message: 'No tasks available' }, 404)
  }

  // Execute claim transition
  const leaseDuration = body.lease_duration_seconds || config.defaultLeaseDurationSeconds
  const transitionResult = await executeTransition(db, task.id, TRANSITIONS.claim, {
    orchestrator_id: body.orchestrator_id,
    agent_name: body.agent_name,
    role_filter: body.role_filter,
    lease_duration_seconds: leaseDuration,
  })

  if (!transitionResult.success) {
    return c.json(
      { error: 'Failed to claim task', details: transitionResult.errors },
      409
    )
  }

  // Update claimed_by and claimed_at
  await execute(
    db,
    `UPDATE tasks
     SET claimed_by = ?,
         claimed_at = datetime('now'),
         updated_at = datetime('now')
     WHERE id = ?`,
    body.agent_name,
    task.id
  )

  // Return claimed task
  const claimedTask = await queryOne<Task>(
    db,
    'SELECT * FROM tasks WHERE id = ?',
    task.id
  )

  return c.json(claimedTask)
})

/**
 * Submit task completion
 * POST /api/v1/tasks/:id/submit
 */
tasksRoute.post('/:id/submit', async (c) => {
  const db = c.env.DB
  const taskId = c.req.param('id')
  const body = (await c.req.json()) as SubmitTaskRequest

  // Validate required fields
  if (body.commits_count === undefined || body.turns_used === undefined) {
    return c.json(
      { error: 'Missing required fields: commits_count, turns_used' },
      400
    )
  }

  // Execute submit transition
  const transitionResult = await executeTransition(db, taskId, TRANSITIONS.submit, {
    commits_count: body.commits_count,
    turns_used: body.turns_used,
  })

  if (!transitionResult.success) {
    return c.json(
      { error: 'Failed to submit task', details: transitionResult.errors },
      409
    )
  }

  // Update task fields
  await execute(
    db,
    `UPDATE tasks
     SET commits_count = ?,
         turns_used = ?,
         check_results = ?,
         submitted_at = datetime('now'),
         updated_at = datetime('now')
     WHERE id = ?`,
    body.commits_count,
    body.turns_used,
    body.check_results || null,
    taskId
  )

  const task = await queryOne<Task>(db, 'SELECT * FROM tasks WHERE id = ?', taskId)

  return c.json(task)
})

/**
 * Accept task
 * POST /api/v1/tasks/:id/accept
 */
tasksRoute.post('/:id/accept', async (c) => {
  const db = c.env.DB
  const taskId = c.req.param('id')
  const body = (await c.req.json()) as AcceptTaskRequest

  if (!body.accepted_by) {
    return c.json({ error: 'Missing required field: accepted_by' }, 400)
  }

  // Execute accept transition
  const transitionResult = await executeTransition(db, taskId, TRANSITIONS.accept, {
    accepted_by: body.accepted_by,
  })

  if (!transitionResult.success) {
    return c.json(
      { error: 'Failed to accept task', details: transitionResult.errors },
      409
    )
  }

  // Update completed_at
  const completedAt = body.completed_at || new Date().toISOString()
  await execute(
    db,
    `UPDATE tasks
     SET completed_at = ?,
         updated_at = datetime('now')
     WHERE id = ?`,
    completedAt,
    taskId
  )

  const task = await queryOne<Task>(db, 'SELECT * FROM tasks WHERE id = ?', taskId)

  return c.json(task)
})

/**
 * Reject task
 * POST /api/v1/tasks/:id/reject
 */
tasksRoute.post('/:id/reject', async (c) => {
  const db = c.env.DB
  const taskId = c.req.param('id')
  const body = (await c.req.json()) as RejectTaskRequest

  if (!body.reason || !body.rejected_by) {
    return c.json(
      { error: 'Missing required fields: reason, rejected_by' },
      400
    )
  }

  // Execute reject transition
  const transitionResult = await executeTransition(db, taskId, TRANSITIONS.reject, {
    reason: body.reason,
    rejected_by: body.rejected_by,
  })

  if (!transitionResult.success) {
    return c.json(
      { error: 'Failed to reject task', details: transitionResult.errors },
      409
    )
  }

  // Increment rejection count
  await execute(
    db,
    `UPDATE tasks
     SET rejection_count = rejection_count + 1,
         claimed_by = NULL,
         claimed_at = NULL,
         orchestrator_id = NULL,
         lease_expires_at = NULL,
         updated_at = datetime('now')
     WHERE id = ?`,
    taskId
  )

  const task = await queryOne<Task>(db, 'SELECT * FROM tasks WHERE id = ?', taskId)

  return c.json(task)
})
