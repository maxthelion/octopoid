/**
 * Projects routes - API endpoints for managing multi-task projects
 * Projects represent containers for related tasks with shared context
 */

import { Hono } from 'hono'
import type { D1Database } from '@cloudflare/workers-types'
import type {
  Project,
  CreateProjectRequest,
  UpdateProjectRequest,
  ProjectFilters,
  ProjectListResponse,
  Task,
} from '@octopoid/shared'

export interface Env {
  DB: D1Database
}

export const projectsRoute = new Hono<{ Bindings: Env }>()

/**
 * Create a new project
 * POST /api/v1/projects
 */
projectsRoute.post('/', async (c) => {
  const db = c.env.DB
  const body = await c.req.json<CreateProjectRequest>()

  // Validate required fields
  if (!body.id || !body.title) {
    return c.json(
      { error: 'Missing required fields: id, title' },
      400
    )
  }

  const now = new Date().toISOString()
  const project: Project = {
    id: body.id,
    title: body.title,
    description: body.description || null,
    status: body.status || 'draft',
    branch: body.branch || null,
    base_branch: body.base_branch || 'main',
    auto_accept: body.auto_accept !== undefined ? body.auto_accept : false,
    created_at: now,
    created_by: body.created_by || null,
    completed_at: null,
  }

  try {
    await db
      .prepare(
        `INSERT INTO projects (
          id, title, description, status, branch, base_branch,
          auto_accept, created_at, created_by, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      .bind(
        project.id,
        project.title,
        project.description,
        project.status,
        project.branch,
        project.base_branch,
        project.auto_accept ? 1 : 0,
        project.created_at,
        project.created_by,
        project.completed_at
      )
      .run()

    return c.json(project, 201)
  } catch (error: any) {
    if (error.message?.includes('UNIQUE constraint failed')) {
      return c.json({ error: `Project ${body.id} already exists` }, 409)
    }
    throw error
  }
})

/**
 * Get a single project by ID
 * GET /api/v1/projects/:id
 */
projectsRoute.get('/:id', async (c) => {
  const db = c.env.DB
  const id = c.req.param('id')

  const result = await db
    .prepare('SELECT * FROM projects WHERE id = ?')
    .bind(id)
    .first<Project>()

  if (!result) {
    return c.json({ error: `Project ${id} not found` }, 404)
  }

  // Convert auto_accept from 0/1 to boolean
  const project = {
    ...result,
    auto_accept: Boolean(result.auto_accept),
  }

  return c.json(project)
})

/**
 * Get all tasks in a project
 * GET /api/v1/projects/:id/tasks
 */
projectsRoute.get('/:id/tasks', async (c) => {
  const db = c.env.DB
  const id = c.req.param('id')

  // Check if project exists
  const project = await db
    .prepare('SELECT id FROM projects WHERE id = ?')
    .bind(id)
    .first()

  if (!project) {
    return c.json({ error: `Project ${id} not found` }, 404)
  }

  // Get all tasks for this project
  const results = await db
    .prepare(
      `SELECT * FROM tasks WHERE project_id = ?
       ORDER BY priority, created_at DESC`
    )
    .bind(id)
    .all<Task>()

  return c.json({
    project_id: id,
    tasks: results.results || [],
    total: results.results?.length || 0,
  })
})

/**
 * List projects with optional filters
 * GET /api/v1/projects
 */
projectsRoute.get('/', async (c) => {
  const db = c.env.DB
  const query = c.req.query()

  // Parse filters
  const filters: ProjectFilters = {
    status: query.status as any,
    created_by: query.created_by,
  }

  // Parse pagination
  const limit = parseInt(query.limit || '50')
  const offset = parseInt(query.offset || '0')

  // Build query
  const conditions: string[] = []
  const params: any[] = []

  if (filters.status) {
    const statuses = Array.isArray(filters.status)
      ? filters.status
      : [filters.status]
    conditions.push(`status IN (${statuses.map(() => '?').join(',')})`)
    params.push(...statuses)
  }

  if (filters.created_by) {
    conditions.push('created_by = ?')
    params.push(filters.created_by)
  }

  const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : ''

  // Get total count
  const countResult = await db
    .prepare(`SELECT COUNT(*) as count FROM projects ${whereClause}`)
    .bind(...params)
    .first<{ count: number }>()

  const total = countResult?.count || 0

  // Get projects
  const results = await db
    .prepare(
      `SELECT * FROM projects ${whereClause}
       ORDER BY created_at DESC
       LIMIT ? OFFSET ?`
    )
    .bind(...params, limit, offset)
    .all<Project>()

  // Convert auto_accept from 0/1 to boolean
  const projects = (results.results || []).map(p => ({
    ...p,
    auto_accept: Boolean(p.auto_accept),
  }))

  const response: ProjectListResponse = {
    projects,
    total,
    offset,
    limit,
  }

  return c.json(response)
})

/**
 * Update a project
 * PATCH /api/v1/projects/:id
 */
projectsRoute.patch('/:id', async (c) => {
  const db = c.env.DB
  const id = c.req.param('id')
  const body = await c.req.json<UpdateProjectRequest>()

  // Check if project exists
  const existing = await db
    .prepare('SELECT * FROM projects WHERE id = ?')
    .bind(id)
    .first<Project>()

  if (!existing) {
    return c.json({ error: `Project ${id} not found` }, 404)
  }

  // Build update query
  const updates: string[] = []
  const params: any[] = []

  if (body.title !== undefined) {
    updates.push('title = ?')
    params.push(body.title)
  }

  if (body.description !== undefined) {
    updates.push('description = ?')
    params.push(body.description)
  }

  if (body.status !== undefined) {
    updates.push('status = ?')
    params.push(body.status)

    // Auto-set completed_at when status becomes completed
    if (body.status === 'completed' && !existing.completed_at) {
      updates.push('completed_at = ?')
      params.push(new Date().toISOString())
    }
  }

  if (body.branch !== undefined) {
    updates.push('branch = ?')
    params.push(body.branch)
  }

  if (body.base_branch !== undefined) {
    updates.push('base_branch = ?')
    params.push(body.base_branch)
  }

  if (body.auto_accept !== undefined) {
    updates.push('auto_accept = ?')
    params.push(body.auto_accept ? 1 : 0)
  }

  if (body.completed_at !== undefined) {
    updates.push('completed_at = ?')
    params.push(body.completed_at)
  }

  if (updates.length === 0) {
    return c.json({ error: 'No fields to update' }, 400)
  }

  // Execute update
  await db
    .prepare(`UPDATE projects SET ${updates.join(', ')} WHERE id = ?`)
    .bind(...params, id)
    .run()

  // Fetch updated project
  const updated = await db
    .prepare('SELECT * FROM projects WHERE id = ?')
    .bind(id)
    .first<Project>()

  if (updated) {
    updated.auto_accept = Boolean(updated.auto_accept)
  }

  return c.json(updated)
})

/**
 * Delete a project
 * DELETE /api/v1/projects/:id
 */
projectsRoute.delete('/:id', async (c) => {
  const db = c.env.DB
  const id = c.req.param('id')

  // Check if project exists
  const existing = await db
    .prepare('SELECT id FROM projects WHERE id = ?')
    .bind(id)
    .first()

  if (!existing) {
    return c.json({ error: `Project ${id} not found` }, 404)
  }

  // Check if project has tasks
  const taskCount = await db
    .prepare('SELECT COUNT(*) as count FROM tasks WHERE project_id = ?')
    .bind(id)
    .first<{ count: number }>()

  if (taskCount && taskCount.count > 0) {
    return c.json(
      {
        error: `Cannot delete project ${id}: has ${taskCount.count} tasks`,
        hint: 'Delete or unlink tasks first',
      },
      409
    )
  }

  // Delete project
  await db
    .prepare('DELETE FROM projects WHERE id = ?')
    .bind(id)
    .run()

  return c.json({ message: `Project ${id} deleted` })
})
