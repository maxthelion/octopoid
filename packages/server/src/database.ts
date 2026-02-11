/**
 * D1 database wrapper for Octopoid server
 * Provides utility functions for database operations
 */

import type { D1Database, D1Result } from '@cloudflare/workers-types'

export interface DatabaseEnv {
  DB: D1Database
}

/**
 * Execute a query and return results
 */
export async function query<T = unknown>(
  db: D1Database,
  sql: string,
  ...params: unknown[]
): Promise<T[]> {
  const result = await db.prepare(sql).bind(...params).all<T>()
  return result.results
}

/**
 * Execute a query and return first result
 */
export async function queryOne<T = unknown>(
  db: D1Database,
  sql: string,
  ...params: unknown[]
): Promise<T | null> {
  const result = await db.prepare(sql).bind(...params).first<T>()
  return result
}

/**
 * Execute a write operation (INSERT, UPDATE, DELETE)
 */
export async function execute(
  db: D1Database,
  sql: string,
  ...params: unknown[]
): Promise<D1Result> {
  return await db.prepare(sql).bind(...params).run()
}

/**
 * Execute multiple statements in a batch (transaction-like)
 * D1 doesn't support traditional transactions, but batch operations are atomic
 */
export async function batch(
  db: D1Database,
  statements: Array<{ sql: string; params?: unknown[] }>
): Promise<D1Result[]> {
  const prepared = statements.map(({ sql, params = [] }) =>
    db.prepare(sql).bind(...params)
  )
  return await db.batch(prepared)
}

/**
 * Get current schema version
 */
export async function getSchemaVersion(db: D1Database): Promise<number | null> {
  try {
    const result = await queryOne<{ value: string }>(
      db,
      'SELECT value FROM schema_info WHERE key = ?',
      'version'
    )
    return result ? parseInt(result.value, 10) : null
  } catch {
    return null
  }
}

/**
 * Health check - verify database is accessible
 */
export async function healthCheck(db: D1Database): Promise<boolean> {
  try {
    await query(db, 'SELECT 1')
    return true
  } catch {
    return false
  }
}
