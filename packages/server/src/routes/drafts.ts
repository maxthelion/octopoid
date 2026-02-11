/**
 * Drafts routes - API endpoints for managing draft documents
 * Drafts represent ideas/proposals that can be converted to tasks or projects
 */

import { Hono } from 'hono'
import type { D1Database } from '@cloudflare/workers-types'
import type {
  Draft,
  CreateDraftRequest,
  UpdateDraftRequest,
  DraftFilters,
  DraftListResponse,
} from '@octopoid/shared'

export interface Env {
  DB: D1Database
}

export const draftsRoute = new Hono<{ Bindings: Env }>()

/**
 * Create a new draft
 * POST /api/v1/drafts
 */
draftsRoute.post('/', async (c) => {
  const db = c.env.DB
  const body = await c.req.json<CreateDraftRequest>()

  // Validate required fields
  if (!body.id || !body.title || !body.author) {
    return c.json(
      { error: 'Missing required fields: id, title, author' },
      400
    )
  }

  const now = new Date().toISOString()
  const draft: Draft = {
    id: body.id,
    title: body.title,
    status: body.status || 'idea',
    author: body.author,
    domain: body.domain || null,
    file_path: body.file_path || null,
    created_at: now,
    updated_at: now,
    linked_task_id: body.linked_task_id || null,
    linked_project_id: body.linked_project_id || null,
    tags: body.tags ? JSON.stringify(body.tags) : null,
  }

  try {
    await db
      .prepare(
        `INSERT INTO drafts (
          id, title, status, author, domain, file_path,
          created_at, updated_at, linked_task_id, linked_project_id, tags
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      .bind(
        draft.id,
        draft.title,
        draft.status,
        draft.author,
        draft.domain,
        draft.file_path,
        draft.created_at,
        draft.updated_at,
        draft.linked_task_id,
        draft.linked_project_id,
        draft.tags
      )
      .run()

    return c.json(draft, 201)
  } catch (error: any) {
    if (error.message?.includes('UNIQUE constraint failed')) {
      return c.json({ error: `Draft ${body.id} already exists` }, 409)
    }
    throw error
  }
})

/**
 * Get a single draft by ID
 * GET /api/v1/drafts/:id
 */
draftsRoute.get('/:id', async (c) => {
  const db = c.env.DB
  const id = c.req.param('id')

  const result = await db
    .prepare('SELECT * FROM drafts WHERE id = ?')
    .bind(id)
    .first<Draft>()

  if (!result) {
    return c.json({ error: `Draft ${id} not found` }, 404)
  }

  return c.json(result)
})

/**
 * List drafts with optional filters
 * GET /api/v1/drafts
 */
draftsRoute.get('/', async (c) => {
  const db = c.env.DB
  const query = c.req.query()

  // Parse filters
  const filters: DraftFilters = {
    status: query.status as any,
    author: query.author,
    domain: query.domain,
    linked_task_id: query.linked_task_id,
    linked_project_id: query.linked_project_id,
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

  if (filters.author) {
    conditions.push('author = ?')
    params.push(filters.author)
  }

  if (filters.domain) {
    conditions.push('domain = ?')
    params.push(filters.domain)
  }

  if (filters.linked_task_id) {
    conditions.push('linked_task_id = ?')
    params.push(filters.linked_task_id)
  }

  if (filters.linked_project_id) {
    conditions.push('linked_project_id = ?')
    params.push(filters.linked_project_id)
  }

  const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : ''

  // Get total count
  const countResult = await db
    .prepare(`SELECT COUNT(*) as count FROM drafts ${whereClause}`)
    .bind(...params)
    .first<{ count: number }>()

  const total = countResult?.count || 0

  // Get drafts
  const results = await db
    .prepare(
      `SELECT * FROM drafts ${whereClause}
       ORDER BY created_at DESC
       LIMIT ? OFFSET ?`
    )
    .bind(...params, limit, offset)
    .all<Draft>()

  const response: DraftListResponse = {
    drafts: results.results || [],
    total,
    offset,
    limit,
  }

  return c.json(response)
})

/**
 * Update a draft
 * PATCH /api/v1/drafts/:id
 */
draftsRoute.patch('/:id', async (c) => {
  const db = c.env.DB
  const id = c.req.param('id')
  const body = await c.req.json<UpdateDraftRequest>()

  // Check if draft exists
  const existing = await db
    .prepare('SELECT * FROM drafts WHERE id = ?')
    .bind(id)
    .first<Draft>()

  if (!existing) {
    return c.json({ error: `Draft ${id} not found` }, 404)
  }

  // Build update query
  const updates: string[] = []
  const params: any[] = []

  if (body.title !== undefined) {
    updates.push('title = ?')
    params.push(body.title)
  }

  if (body.status !== undefined) {
    updates.push('status = ?')
    params.push(body.status)
  }

  if (body.author !== undefined) {
    updates.push('author = ?')
    params.push(body.author)
  }

  if (body.domain !== undefined) {
    updates.push('domain = ?')
    params.push(body.domain)
  }

  if (body.file_path !== undefined) {
    updates.push('file_path = ?')
    params.push(body.file_path)
  }

  if (body.linked_task_id !== undefined) {
    updates.push('linked_task_id = ?')
    params.push(body.linked_task_id)
  }

  if (body.linked_project_id !== undefined) {
    updates.push('linked_project_id = ?')
    params.push(body.linked_project_id)
  }

  if (body.tags !== undefined) {
    updates.push('tags = ?')
    params.push(JSON.stringify(body.tags))
  }

  // Always update updated_at
  updates.push('updated_at = ?')
  params.push(new Date().toISOString())

  if (updates.length === 1) {
    // Only updated_at changed
    return c.json({ error: 'No fields to update' }, 400)
  }

  // Execute update
  await db
    .prepare(`UPDATE drafts SET ${updates.join(', ')} WHERE id = ?`)
    .bind(...params, id)
    .run()

  // Fetch updated draft
  const updated = await db
    .prepare('SELECT * FROM drafts WHERE id = ?')
    .bind(id)
    .first<Draft>()

  return c.json(updated)
})

/**
 * Delete a draft
 * DELETE /api/v1/drafts/:id
 */
draftsRoute.delete('/:id', async (c) => {
  const db = c.env.DB
  const id = c.req.param('id')

  // Check if draft exists
  const existing = await db
    .prepare('SELECT id FROM drafts WHERE id = ?')
    .bind(id)
    .first()

  if (!existing) {
    return c.json({ error: `Draft ${id} not found` }, 404)
  }

  // Delete draft
  await db
    .prepare('DELETE FROM drafts WHERE id = ?')
    .bind(id)
    .run()

  return c.json({ message: `Draft ${id} deleted` })
})
