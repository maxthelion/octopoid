/**
 * Database interface - backend abstraction with offline mode
 * Transparently switches between local SQLite and remote API
 * Falls back to local cache when server is unavailable
 */

import type {
  Task,
  CreateTaskRequest,
  UpdateTaskRequest,
  ClaimTaskRequest,
  SubmitTaskRequest,
  AcceptTaskRequest,
  RejectTaskRequest,
  TaskFilters,
} from '@octopoid/shared'
import { OctopoidAPIClient } from './api-client'
import { LocalCache } from './local-cache'
import { SyncManager } from './sync-manager'
import { loadConfig, isRemoteMode } from './config'

// Singleton instances
let apiClient: OctopoidAPIClient | null = null
let localCache: LocalCache | null = null
let syncManager: SyncManager | null = null
let offlineMode = false

/**
 * Check if error is a network error (server unreachable)
 */
function isNetworkError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false
  }

  const message = error.message.toLowerCase()
  return (
    message.includes('fetch failed') ||
    message.includes('econnrefused') ||
    message.includes('enotfound') ||
    message.includes('etimedout') ||
    message.includes('network') ||
    message.includes('timeout')
  )
}

/**
 * Get API client instance
 */
function getAPIClient(): OctopoidAPIClient {
  if (!apiClient) {
    const config = loadConfig()

    if (!isRemoteMode() || !config.server?.url) {
      throw new Error('Not configured for remote mode')
    }

    apiClient = new OctopoidAPIClient(config.server.url, {
      apiKey: config.server.api_key,
    })
  }

  return apiClient
}

/**
 * Get local cache instance
 */
function getLocalCache(): LocalCache {
  if (!localCache) {
    localCache = new LocalCache()
  }
  return localCache
}

/**
 * Get or start sync manager
 */
function getSyncManager(): SyncManager | null {
  if (!isRemoteMode()) {
    return null
  }

  if (!syncManager) {
    const client = getAPIClient()
    const cache = getLocalCache()
    syncManager = new SyncManager(client, cache)
    syncManager.start()
  }

  return syncManager
}

/**
 * Reset backend (for testing)
 */
export function resetBackend(): void {
  if (syncManager) {
    syncManager.stop()
    syncManager = null
  }
  if (localCache) {
    localCache.close()
    localCache = null
  }
  apiClient = null
  offlineMode = false
}

/**
 * Check if currently in offline mode
 */
export function isOfflineMode(): boolean {
  return offlineMode
}

/**
 * Get sync status (for status command)
 */
export function getSyncStatus(): {
  mode: 'online' | 'offline'
  pending?: number
  failed?: number
} {
  if (!isRemoteMode()) {
    return { mode: 'online' }
  }

  if (!offlineMode) {
    return { mode: 'online' }
  }

  const manager = getSyncManager()
  if (!manager) {
    return { mode: 'offline' }
  }

  const status = manager.getStatus()
  return {
    mode: 'offline',
    pending: status.pending,
    failed: status.failed,
  }
}

// Re-export all operations with offline fallback

export async function listTasks(
  filters?: TaskFilters & { limit?: number; offset?: number }
): Promise<Task[]> {
  if (!isRemoteMode()) {
    // Local mode only
    const cache = getLocalCache()
    return await cache.listTasks(filters)
  }

  try {
    // Try server first
    const client = getAPIClient()
    const response = await client.listTasks(filters)
    offlineMode = false
    return response.tasks
  } catch (error) {
    if (isNetworkError(error)) {
      // Fall back to local cache
      if (!offlineMode) {
        console.warn('⚠️  Server unreachable, using local cache')
        offlineMode = true
      }
      const cache = getLocalCache()
      return await cache.listTasks(filters)
    }
    throw error
  }
}

export async function getTask(taskId: string): Promise<Task | null> {
  if (!isRemoteMode()) {
    const cache = getLocalCache()
    return cache.getTask(taskId)
  }

  try {
    const client = getAPIClient()
    const task = await client.getTask(taskId)
    offlineMode = false
    return task
  } catch (error) {
    if (isNetworkError(error)) {
      if (!offlineMode) {
        console.warn('⚠️  Server unreachable, using local cache')
        offlineMode = true
      }
      const cache = getLocalCache()
      return cache.getTask(taskId)
    }

    if (error instanceof Error && error.message.includes('404')) {
      return null
    }

    throw error
  }
}

export async function createTask(request: CreateTaskRequest): Promise<Task> {
  if (!isRemoteMode()) {
    const cache = getLocalCache()
    return await cache.createTask(request)
  }

  try {
    const client = getAPIClient()
    const task = await client.createTask(request)
    offlineMode = false
    return task
  } catch (error) {
    if (isNetworkError(error)) {
      if (!offlineMode) {
        console.warn('⚠️  Server unreachable, saving locally')
        offlineMode = true
      }
      const cache = getLocalCache()
      const task = await cache.createTask(request)

      // Start sync manager if not running
      getSyncManager()

      console.log('✓ Task created locally, will sync when server available')
      return task
    }
    throw error
  }
}

export async function updateTask(
  taskId: string,
  request: UpdateTaskRequest
): Promise<Task> {
  if (!isRemoteMode()) {
    const cache = getLocalCache()
    return (await cache.updateTask(taskId, request))!
  }

  try {
    const client = getAPIClient()
    const task = await client.updateTask(taskId, request)
    offlineMode = false
    return task
  } catch (error) {
    if (isNetworkError(error)) {
      if (!offlineMode) {
        console.warn('⚠️  Server unreachable, saving locally')
        offlineMode = true
      }
      const cache = getLocalCache()
      const task = await cache.updateTask(taskId, request)

      if (task) {
        getSyncManager()
        return task
      }

      throw new Error(`Task ${taskId} not found in local cache`)
    }
    throw error
  }
}

export async function claimTask(
  request: ClaimTaskRequest
): Promise<Task | null> {
  if (!isRemoteMode()) {
    const cache = getLocalCache()
    return await cache.claimTask(request)
  }

  try {
    const client = getAPIClient()
    const task = await client.claimTask(request)
    offlineMode = false
    return task
  } catch (error) {
    if (isNetworkError(error)) {
      if (!offlineMode) {
        console.warn('⚠️  Server unreachable, using local cache')
        offlineMode = true
      }
      const cache = getLocalCache()
      return await cache.claimTask(request)
    }
    throw error
  }
}

export async function submitCompletion(
  taskId: string,
  request: SubmitTaskRequest
): Promise<Task> {
  if (!isRemoteMode()) {
    const cache = getLocalCache()
    return (await cache.submitTaskCompletion(taskId, request))!
  }

  try {
    const client = getAPIClient()
    const task = await client.submitCompletion(taskId, request)
    offlineMode = false
    return task
  } catch (error) {
    if (isNetworkError(error)) {
      if (!offlineMode) {
        console.warn('⚠️  Server unreachable, saving locally')
        offlineMode = true
      }
      const cache = getLocalCache()
      const task = await cache.submitTaskCompletion(taskId, request)

      if (task) {
        getSyncManager()
        return task
      }

      throw new Error(`Task ${taskId} not found in local cache`)
    }
    throw error
  }
}

export async function acceptCompletion(
  taskId: string,
  request: AcceptTaskRequest
): Promise<Task> {
  if (!isRemoteMode()) {
    const cache = getLocalCache()
    return (await cache.acceptTask(taskId, request))!
  }

  try {
    const client = getAPIClient()
    const task = await client.acceptCompletion(taskId, request)
    offlineMode = false
    return task
  } catch (error) {
    if (isNetworkError(error)) {
      if (!offlineMode) {
        console.warn('⚠️  Server unreachable, saving locally')
        offlineMode = true
      }
      const cache = getLocalCache()
      const task = await cache.acceptTask(taskId, request)

      if (task) {
        getSyncManager()
        return task
      }

      throw new Error(`Task ${taskId} not found in local cache`)
    }
    throw error
  }
}

export async function rejectCompletion(
  taskId: string,
  request: RejectTaskRequest
): Promise<Task> {
  if (!isRemoteMode()) {
    const cache = getLocalCache()
    return (await cache.rejectTask(taskId, request))!
  }

  try {
    const client = getAPIClient()
    const task = await client.rejectCompletion(taskId, request)
    offlineMode = false
    return task
  } catch (error) {
    if (isNetworkError(error)) {
      if (!offlineMode) {
        console.warn('⚠️  Server unreachable, saving locally')
        offlineMode = true
      }
      const cache = getLocalCache()
      const task = await cache.rejectTask(taskId, request)

      if (task) {
        getSyncManager()
        return task
      }

      throw new Error(`Task ${taskId} not found in local cache`)
    }
    throw error
  }
}

// Health check
export async function healthCheck(): Promise<boolean> {
  try {
    const client = getAPIClient()
    const response = await client.healthCheck()
    offlineMode = false
    return response.status === 'healthy'
  } catch {
    offlineMode = true
    return false
  }
}
