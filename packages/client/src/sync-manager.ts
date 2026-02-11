/**
 * Sync Manager - handles background syncing between local cache and server
 *
 * When server is unavailable:
 * - Operations are queued in local cache
 * - Periodically tries to reconnect
 *
 * When server becomes available:
 * - Syncs queued operations
 * - Resolves conflicts (server state wins)
 * - Notifies user of sync status
 */

import type { OctopoidAPIClient } from './api-client'
import type { LocalCache } from './local-cache'

export interface SyncResult {
  succeeded: number
  failed: number
  errors: Array<{ operation: string; task_id: string; error: string }>
}

/**
 * Background sync manager
 */
export class SyncManager {
  private client: OctopoidAPIClient
  private cache: LocalCache
  private syncInterval: number
  private syncTimer: NodeJS.Timeout | null = null
  private syncing = false

  constructor(
    client: OctopoidAPIClient,
    cache: LocalCache,
    syncInterval = 30000 // 30 seconds default
  ) {
    this.client = client
    this.cache = cache
    this.syncInterval = syncInterval
  }

  /**
   * Start background sync process
   */
  start(): void {
    if (this.syncTimer) {
      return // Already running
    }

    console.log('🔄 Starting background sync manager')

    // Run initial sync
    this.sync().catch((error) => {
      console.error('Initial sync failed:', error)
    })

    // Schedule periodic syncs
    this.syncTimer = setInterval(() => {
      this.sync().catch((error) => {
        console.error('Background sync failed:', error)
      })
    }, this.syncInterval)
  }

  /**
   * Stop background sync process
   */
  stop(): void {
    if (this.syncTimer) {
      clearInterval(this.syncTimer)
      this.syncTimer = null
      console.log('⏸️  Stopped background sync manager')
    }
  }

  /**
   * Perform sync operation
   */
  async sync(): Promise<SyncResult> {
    if (this.syncing) {
      return { succeeded: 0, failed: 0, errors: [] }
    }

    this.syncing = true

    try {
      // Check server connectivity first
      const isOnline = await this.checkServerConnectivity()
      if (!isOnline) {
        // Server unavailable, skip sync
        return { succeeded: 0, failed: 0, errors: [] }
      }

      // Get pending operations
      const pendingOps = this.cache.getPendingSyncOps()

      if (pendingOps.length === 0) {
        return { succeeded: 0, failed: 0, errors: [] }
      }

      console.log(`🔄 Syncing ${pendingOps.length} pending operations...`)

      const result: SyncResult = {
        succeeded: 0,
        failed: 0,
        errors: [],
      }

      // Process each operation
      for (const op of pendingOps) {
        try {
          await this.syncOperation(op)
          this.cache.markSynced(op.id)
          result.succeeded++
        } catch (error) {
          const errorMsg = error instanceof Error ? error.message : String(error)
          this.cache.markSyncFailed(op.id, errorMsg)
          result.failed++
          result.errors.push({
            operation: op.operation,
            task_id: op.task_id,
            error: errorMsg,
          })
        }
      }

      if (result.succeeded > 0) {
        console.log(`✅ Synced ${result.succeeded} operations`)
      }

      if (result.failed > 0) {
        console.warn(`⚠️  Failed to sync ${result.failed} operations`)
      }

      return result
    } finally {
      this.syncing = false
    }
  }

  /**
   * Sync a single operation to server
   */
  private async syncOperation(op: {
    id: number
    operation: string
    task_id: string
    payload: any
  }): Promise<void> {
    const { operation, task_id, payload } = op

    switch (operation) {
      case 'create':
        await this.client.request('POST', '/api/v1/tasks', payload)
        break

      case 'claim':
        await this.client.request('POST', '/api/v1/tasks/claim', payload)
        break

      case 'submit':
        await this.client.request(
          'POST',
          `/api/v1/tasks/${task_id}/submit`,
          payload
        )
        break

      case 'accept':
        await this.client.request(
          'POST',
          `/api/v1/tasks/${task_id}/accept`,
          payload
        )
        break

      case 'reject':
        await this.client.request(
          'POST',
          `/api/v1/tasks/${task_id}/reject`,
          payload
        )
        break

      case 'update':
        await this.client.request(
          'PATCH',
          `/api/v1/tasks/${task_id}`,
          payload
        )
        break

      default:
        throw new Error(`Unknown sync operation: ${operation}`)
    }
  }

  /**
   * Check if server is reachable
   */
  private async checkServerConnectivity(): Promise<boolean> {
    try {
      await this.client.request('GET', '/api/health')
      return true
    } catch (error) {
      return false
    }
  }

  /**
   * Get sync status
   */
  getStatus(): {
    pending: number
    failed: number
    syncing: boolean
  } {
    const { pending, failed } = this.cache.getSyncStatus()
    return {
      pending,
      failed,
      syncing: this.syncing,
    }
  }

  /**
   * Force immediate sync
   */
  async forcSync(): Promise<SyncResult> {
    return await this.sync()
  }
}
