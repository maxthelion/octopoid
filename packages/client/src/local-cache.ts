/**
 * Local cache for offline mode
 * Uses SQLite to cache tasks and state when server is unreachable
 */

import Database from 'better-sqlite3'
import { join } from 'node:path'
import { existsSync, mkdirSync } from 'node:fs'
import type {
  Task,
  ClaimTaskRequest,
  SubmitTaskRequest,
  AcceptTaskRequest,
  RejectTaskRequest,
  CreateTaskRequest,
  UpdateTaskRequest,
  ListTasksRequest,
} from '@octopoid/shared'
import { getRuntimeDir } from './config'

/**
 * Local SQLite cache for offline operation
 */
export class LocalCache {
  private db: Database.Database
  private dbPath: string

  constructor(dbPath?: string) {
    // Default to .octopoid/runtime/cache.db
    this.dbPath = dbPath || join(getRuntimeDir(), 'cache.db')

    // Ensure directory exists
    const dir = join(this.dbPath, '..')
    mkdirSync(dir, { recursive: true })

    // Open database
    this.db = new Database(this.dbPath)

    // Initialize schema
    this.initSchema()
  }

  /**
   * Initialize database schema
   */
  private initSchema(): void {
    // Tasks table (simplified version of server schema)
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        file_path TEXT NOT NULL,
        queue TEXT NOT NULL,
        priority TEXT NOT NULL,
        role TEXT,
        branch TEXT DEFAULT 'main',
        project_id TEXT,
        created_by TEXT,
        claimed_by TEXT,
        claimed_at TEXT,
        submitted_at TEXT,
        completed_at TEXT,
        pr_url TEXT,
        commits_count INTEGER DEFAULT 0,
        turns_used INTEGER DEFAULT 0,
        blocked_by TEXT,
        version INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
      )
    `)

    // Sync queue table (tracks operations to sync to server)
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS sync_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation TEXT NOT NULL,
        task_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        synced_at TEXT,
        error TEXT
      )
    `)

    // Indexes for performance
    this.db.exec(`
      CREATE INDEX IF NOT EXISTS idx_tasks_queue ON tasks(queue)
    `)
    this.db.exec(`
      CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)
    `)
    this.db.exec(`
      CREATE INDEX IF NOT EXISTS idx_sync_queue_pending ON sync_queue(synced_at) WHERE synced_at IS NULL
    `)
  }

  /**
   * Create a task in local cache
   */
  async createTask(request: CreateTaskRequest): Promise<Task> {
    const now = new Date().toISOString()

    const stmt = this.db.prepare(`
      INSERT INTO tasks (
        id, file_path, queue, priority, role, branch, project_id,
        created_by, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `)

    stmt.run(
      request.id,
      request.file_path,
      request.queue || 'incoming',
      request.priority || 'P2',
      request.role || null,
      request.branch || 'main',
      request.project_id || null,
      request.created_by || null,
      now,
      now
    )

    // Queue for sync
    this.queueForSync('create', request.id, request)

    return this.getTask(request.id)!
  }

  /**
   * Get a task by ID
   */
  getTask(taskId: string): Task | null {
    const stmt = this.db.prepare('SELECT * FROM tasks WHERE id = ?')
    const row = stmt.get(taskId) as any

    if (!row) {
      return null
    }

    return this.rowToTask(row)
  }

  /**
   * List tasks
   */
  async listTasks(request: ListTasksRequest = {}): Promise<Task[]> {
    let query = 'SELECT * FROM tasks WHERE 1=1'
    const params: any[] = []

    if (request.queue) {
      query += ' AND queue = ?'
      params.push(request.queue)
    }

    if (request.priority) {
      query += ' AND priority = ?'
      params.push(request.priority)
    }

    if (request.role) {
      query += ' AND role = ?'
      params.push(request.role)
    }

    // Order by priority, then created_at
    query += ' ORDER BY priority ASC, created_at DESC'

    if (request.limit) {
      query += ' LIMIT ?'
      params.push(request.limit)
    }

    const stmt = this.db.prepare(query)
    const rows = stmt.all(...params) as any[]

    return rows.map((row) => this.rowToTask(row))
  }

  /**
   * Claim a task
   */
  async claimTask(request: ClaimTaskRequest): Promise<Task | null> {
    // Find first available task
    const tasks = await this.listTasks({
      queue: 'incoming',
      role: request.role_filter,
      limit: 1,
    })

    if (tasks.length === 0) {
      return null
    }

    const task = tasks[0]
    const now = new Date().toISOString()

    // Update task to claimed
    const stmt = this.db.prepare(`
      UPDATE tasks
      SET queue = 'claimed',
          claimed_by = ?,
          claimed_at = ?,
          updated_at = ?
      WHERE id = ?
    `)

    stmt.run(request.agent_name, now, now, task.id)

    // Queue for sync
    this.queueForSync('claim', task.id, request)

    return this.getTask(task.id)
  }

  /**
   * Submit task completion
   */
  async submitTaskCompletion(
    taskId: string,
    request: SubmitTaskRequest
  ): Promise<Task | null> {
    const now = new Date().toISOString()

    const stmt = this.db.prepare(`
      UPDATE tasks
      SET queue = 'provisional',
          pr_url = ?,
          commits_count = ?,
          turns_used = ?,
          submitted_at = ?,
          updated_at = ?
      WHERE id = ?
    `)

    stmt.run(
      request.pr_url || null,
      request.commits_count || 0,
      request.turns_used || 0,
      now,
      now,
      taskId
    )

    // Queue for sync
    this.queueForSync('submit', taskId, request)

    return this.getTask(taskId)
  }

  /**
   * Accept task
   */
  async acceptTask(
    taskId: string,
    request: AcceptTaskRequest
  ): Promise<Task | null> {
    const now = new Date().toISOString()

    const stmt = this.db.prepare(`
      UPDATE tasks
      SET queue = 'done',
          completed_at = ?,
          updated_at = ?
      WHERE id = ?
    `)

    stmt.run(now, now, taskId)

    // Queue for sync
    this.queueForSync('accept', taskId, request)

    return this.getTask(taskId)
  }

  /**
   * Reject task
   */
  async rejectTask(
    taskId: string,
    request: RejectTaskRequest
  ): Promise<Task | null> {
    const now = new Date().toISOString()

    const stmt = this.db.prepare(`
      UPDATE tasks
      SET queue = 'incoming',
          claimed_by = NULL,
          claimed_at = NULL,
          updated_at = ?
      WHERE id = ?
    `)

    stmt.run(now, taskId)

    // Queue for sync
    this.queueForSync('reject', taskId, request)

    return this.getTask(taskId)
  }

  /**
   * Update task
   */
  async updateTask(
    taskId: string,
    updates: UpdateTaskRequest
  ): Promise<Task | null> {
    const fields: string[] = []
    const values: any[] = []

    // Build dynamic update query
    for (const [key, value] of Object.entries(updates)) {
      if (value !== undefined) {
        fields.push(`${key} = ?`)
        values.push(value)
      }
    }

    if (fields.length === 0) {
      return this.getTask(taskId)
    }

    fields.push('updated_at = ?')
    values.push(new Date().toISOString())
    values.push(taskId)

    const query = `UPDATE tasks SET ${fields.join(', ')} WHERE id = ?`
    const stmt = this.db.prepare(query)
    stmt.run(...values)

    // Queue for sync
    this.queueForSync('update', taskId, updates)

    return this.getTask(taskId)
  }

  /**
   * Queue an operation for sync to server
   */
  private queueForSync(
    operation: string,
    taskId: string,
    payload: any
  ): void {
    const stmt = this.db.prepare(`
      INSERT INTO sync_queue (operation, task_id, payload)
      VALUES (?, ?, ?)
    `)

    stmt.run(operation, taskId, JSON.stringify(payload))
  }

  /**
   * Get pending sync operations
   */
  getPendingSyncOps(): Array<{
    id: number
    operation: string
    task_id: string
    payload: any
    created_at: string
  }> {
    const stmt = this.db.prepare(`
      SELECT * FROM sync_queue
      WHERE synced_at IS NULL
      ORDER BY id ASC
    `)

    const rows = stmt.all() as any[]
    return rows.map((row) => ({
      ...row,
      payload: JSON.parse(row.payload),
    }))
  }

  /**
   * Mark sync operation as completed
   */
  markSynced(syncId: number): void {
    const stmt = this.db.prepare(`
      UPDATE sync_queue
      SET synced_at = ?
      WHERE id = ?
    `)

    stmt.run(new Date().toISOString(), syncId)
  }

  /**
   * Mark sync operation as failed
   */
  markSyncFailed(syncId: number, error: string): void {
    const stmt = this.db.prepare(`
      UPDATE sync_queue
      SET error = ?
      WHERE id = ?
    `)

    stmt.run(error, syncId)
  }

  /**
   * Get sync queue status
   */
  getSyncStatus(): {
    pending: number
    failed: number
  } {
    const pendingStmt = this.db.prepare(`
      SELECT COUNT(*) as count FROM sync_queue
      WHERE synced_at IS NULL AND error IS NULL
    `)
    const pending = (pendingStmt.get() as any).count

    const failedStmt = this.db.prepare(`
      SELECT COUNT(*) as count FROM sync_queue
      WHERE error IS NOT NULL
    `)
    const failed = (failedStmt.get() as any).count

    return { pending, failed }
  }

  /**
   * Convert database row to Task object
   */
  private rowToTask(row: any): Task {
    return {
      id: row.id,
      file_path: row.file_path,
      queue: row.queue,
      priority: row.priority,
      role: row.role,
      branch: row.branch,
      project_id: row.project_id,
      created_by: row.created_by,
      claimed_by: row.claimed_by,
      claimed_at: row.claimed_at,
      submitted_at: row.submitted_at,
      completed_at: row.completed_at,
      pr_url: row.pr_url,
      commits_count: row.commits_count,
      turns_used: row.turns_used,
      blocked_by: row.blocked_by,
      version: row.version,
      created_at: row.created_at,
      updated_at: row.updated_at,
    }
  }

  /**
   * Close database connection
   */
  close(): void {
    this.db.close()
  }
}
