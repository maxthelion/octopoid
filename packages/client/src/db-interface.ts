/**
 * Database interface - backend abstraction
 * Transparently switches between local SQLite and remote API
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
import { loadConfig, isRemoteMode } from './config'

// Singleton backend instance
let backend: OctopoidAPIClient | null = null

/**
 * Get backend instance (API client or local database)
 */
function getBackend(): OctopoidAPIClient {
  if (!backend) {
    const config = loadConfig()

    if (isRemoteMode()) {
      // Remote mode - use API client
      if (!config.server?.url) {
        throw new Error('Server URL not configured')
      }

      backend = new OctopoidAPIClient(config.server.url, {
        apiKey: config.server.api_key,
      })
    } else {
      // Local mode - use local database (to be implemented)
      throw new Error('Local mode not yet implemented - use remote mode')
    }
  }

  return backend
}

/**
 * Reset backend (for testing)
 */
export function resetBackend(): void {
  backend = null
}

// Re-export all operations with unified interface

export async function listTasks(
  filters?: TaskFilters & { limit?: number; offset?: number }
): Promise<Task[]> {
  const response = await getBackend().listTasks(filters)
  return response.tasks
}

export async function getTask(taskId: string): Promise<Task | null> {
  try {
    return await getBackend().getTask(taskId)
  } catch (error) {
    if (error instanceof Error && error.message.includes('404')) {
      return null
    }
    throw error
  }
}

export async function createTask(request: CreateTaskRequest): Promise<Task> {
  return await getBackend().createTask(request)
}

export async function updateTask(
  taskId: string,
  request: UpdateTaskRequest
): Promise<Task> {
  return await getBackend().updateTask(taskId, request)
}

export async function claimTask(request: ClaimTaskRequest): Promise<Task | null> {
  return await getBackend().claimTask(request)
}

export async function submitCompletion(
  taskId: string,
  request: SubmitTaskRequest
): Promise<Task> {
  return await getBackend().submitCompletion(taskId, request)
}

export async function acceptCompletion(
  taskId: string,
  request: AcceptTaskRequest
): Promise<Task> {
  return await getBackend().acceptCompletion(taskId, request)
}

export async function rejectCompletion(
  taskId: string,
  request: RejectTaskRequest
): Promise<Task> {
  return await getBackend().rejectCompletion(taskId, request)
}

// Health check
export async function healthCheck(): Promise<boolean> {
  try {
    const response = await getBackend().healthCheck()
    return response.status === 'healthy'
  } catch {
    return false
  }
}
