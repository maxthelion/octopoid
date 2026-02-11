/**
 * Octopoid SDK for Node.js/TypeScript
 *
 * Write custom scripts and automation for your Octopoid orchestrator.
 *
 * @example
 * ```typescript
 * import { OctopoidSDK } from 'octopoid-sdk'
 *
 * const sdk = new OctopoidSDK({
 *   serverUrl: 'https://octopoid-server.username.workers.dev',
 *   apiKey: process.env.OCTOPOID_API_KEY
 * })
 *
 * // Create a task
 * const task = await sdk.tasks.create({
 *   id: 'my-task-123',
 *   file_path: 'tasks/incoming/TASK-my-task-123.md',
 *   queue: 'incoming',
 *   priority: 'P1',
 *   role: 'implement'
 * })
 *
 * // List tasks
 * const tasks = await sdk.tasks.list({ queue: 'incoming' })
 *
 * // Get a task
 * const task = await sdk.tasks.get('my-task-123')
 *
 * // Update a task
 * await sdk.tasks.update('my-task-123', { priority: 'P0' })
 * ```
 */

import {
  OctopoidAPIClient,
  type Task,
  type CreateTaskRequest,
  type UpdateTaskRequest,
  type ClaimTaskRequest,
  type SubmitTaskRequest,
  type AcceptTaskRequest,
  type RejectTaskRequest,
  type TaskFilters,
  type Project,
  type CreateProjectRequest,
} from 'octopoid'

export interface SDKConfig {
  /**
   * Server URL (e.g., https://octopoid-server.username.workers.dev)
   * Required for remote mode
   */
  serverUrl?: string

  /**
   * API key for authentication
   * Can also be set via OCTOPOID_API_KEY environment variable
   */
  apiKey?: string

  /**
   * Request timeout in milliseconds
   * @default 30000 (30 seconds)
   */
  timeout?: number
}

/**
 * Main SDK class for interacting with Octopoid
 */
export class OctopoidSDK {
  private client: OctopoidAPIClient

  constructor(config: SDKConfig = {}) {
    const serverUrl = config.serverUrl || process.env.OCTOPOID_SERVER_URL
    if (!serverUrl) {
      throw new Error(
        'Server URL required. Set via config or OCTOPOID_SERVER_URL environment variable'
      )
    }

    const apiKey = config.apiKey || process.env.OCTOPOID_API_KEY

    this.client = new OctopoidAPIClient(serverUrl, {
      apiKey,
      timeout: config.timeout,
    })
  }

  /**
   * Task operations
   */
  readonly tasks = {
    /**
     * Create a new task
     */
    create: async (request: CreateTaskRequest): Promise<Task> => {
      return await this.client.createTask(request)
    },

    /**
     * Get a task by ID
     */
    get: async (taskId: string): Promise<Task | null> => {
      try {
        return await this.client.getTask(taskId)
      } catch (error) {
        if (error instanceof Error && error.message.includes('404')) {
          return null
        }
        throw error
      }
    },

    /**
     * List tasks with optional filters
     */
    list: async (
      filters?: TaskFilters & { limit?: number; offset?: number }
    ): Promise<Task[]> => {
      const response = await this.client.listTasks(filters)
      return response.tasks
    },

    /**
     * Update a task
     */
    update: async (
      taskId: string,
      updates: UpdateTaskRequest
    ): Promise<Task> => {
      return await this.client.updateTask(taskId, updates)
    },

    /**
     * Claim a task (for agents)
     */
    claim: async (request: ClaimTaskRequest): Promise<Task | null> => {
      return await this.client.claimTask(request)
    },

    /**
     * Submit task completion (for agents)
     */
    submit: async (
      taskId: string,
      request: SubmitTaskRequest
    ): Promise<Task> => {
      return await this.client.submitCompletion(taskId, request)
    },

    /**
     * Accept a completed task
     */
    accept: async (
      taskId: string,
      request: AcceptTaskRequest
    ): Promise<Task> => {
      return await this.client.acceptCompletion(taskId, request)
    },

    /**
     * Reject a completed task
     */
    reject: async (
      taskId: string,
      request: RejectTaskRequest
    ): Promise<Task> => {
      return await this.client.rejectCompletion(taskId, request)
    },
  }

  /**
   * Project operations
   */
  readonly projects = {
    /**
     * Create a new project
     */
    create: async (request: CreateProjectRequest): Promise<Project> => {
      return await this.client.createProject(request)
    },

    /**
     * Get a project by ID
     */
    get: async (projectId: string): Promise<Project | null> => {
      try {
        return await this.client.getProject(projectId)
      } catch (error) {
        if (error instanceof Error && error.message.includes('404')) {
          return null
        }
        throw error
      }
    },

    /**
     * List all projects
     */
    list: async (): Promise<Project[]> => {
      const response = await this.client.listProjects()
      return response.projects
    },
  }

  /**
   * System operations
   */
  readonly system = {
    /**
     * Check server health
     */
    healthCheck: async (): Promise<{ status: string }> => {
      return await this.client.healthCheck()
    },
  }
}

/**
 * Re-export types for convenience
 */
export type {
  Task,
  CreateTaskRequest,
  UpdateTaskRequest,
  ClaimTaskRequest,
  SubmitTaskRequest,
  AcceptTaskRequest,
  RejectTaskRequest,
  TaskFilters,
  Project,
  CreateProjectRequest,
}

/**
 * Default export for CommonJS compatibility
 */
export default OctopoidSDK
