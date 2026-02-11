/**
 * API client for Octopoid server
 * Wraps REST API calls with TypeScript types
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
  TaskListResponse,
  Orchestrator,
  RegisterOrchestratorRequest,
  RegisterOrchestratorResponse,
  HeartbeatRequest,
  HeartbeatResponse,
  OrchestratorListResponse,
  HealthCheckResponse,
} from '@octopoid/shared'

export class OctopoidAPIClient {
  private baseUrl: string
  private timeout: number
  private apiKey?: string

  constructor(baseUrl: string, options: { timeout?: number; apiKey?: string } = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, '') // Remove trailing slash
    this.timeout = options.timeout || 30000
    this.apiKey = options.apiKey
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown
  ): Promise<T | null> {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), this.timeout)

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }

    if (this.apiKey) {
      headers['Authorization'] = `Bearer ${this.apiKey}`
    }

    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      })

      if (response.status === 204) {
        return null
      }

      if (!response.ok) {
        const error = await response.json().catch(() => ({ error: 'Unknown error' }))
        throw new Error(
          `API error (${response.status}): ${error.error || response.statusText}`
        )
      }

      return (await response.json()) as T
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        throw new Error(`Request timeout after ${this.timeout}ms`)
      }
      throw error
    } finally {
      clearTimeout(timeoutId)
    }
  }

  // Health check
  async healthCheck(): Promise<HealthCheckResponse> {
    return (await this.request<HealthCheckResponse>('GET', '/api/health'))!
  }

  // Orchestrator operations
  async registerOrchestrator(
    request: RegisterOrchestratorRequest
  ): Promise<RegisterOrchestratorResponse> {
    return (await this.request<RegisterOrchestratorResponse>(
      'POST',
      '/api/v1/orchestrators/register',
      request
    ))!
  }

  async sendHeartbeat(
    orchestratorId: string,
    request: HeartbeatRequest
  ): Promise<HeartbeatResponse> {
    return (await this.request<HeartbeatResponse>(
      'POST',
      `/api/v1/orchestrators/${orchestratorId}/heartbeat`,
      request
    ))!
  }

  async listOrchestrators(filters?: {
    cluster?: string
    status?: string
  }): Promise<OrchestratorListResponse> {
    const params = new URLSearchParams()
    if (filters?.cluster) params.append('cluster', filters.cluster)
    if (filters?.status) params.append('status', filters.status)

    const query = params.toString()
    return (await this.request<OrchestratorListResponse>(
      'GET',
      `/api/v1/orchestrators${query ? '?' + query : ''}`
    ))!
  }

  async getOrchestrator(orchestratorId: string): Promise<Orchestrator> {
    return (await this.request<Orchestrator>(
      'GET',
      `/api/v1/orchestrators/${orchestratorId}`
    ))!
  }

  // Task operations
  async listTasks(filters?: TaskFilters & { limit?: number; offset?: number }): Promise<TaskListResponse> {
    const params = new URLSearchParams()

    if (filters?.queue) {
      const queues = Array.isArray(filters.queue) ? filters.queue : [filters.queue]
      params.append('queue', queues.join(','))
    }
    if (filters?.priority) {
      const priorities = Array.isArray(filters.priority) ? filters.priority : [filters.priority]
      params.append('priority', priorities.join(','))
    }
    if (filters?.role) {
      const roles = Array.isArray(filters.role) ? filters.role : [filters.role]
      params.append('role', roles.join(','))
    }
    if (filters?.claimed_by) params.append('claimed_by', filters.claimed_by)
    if (filters?.project_id) params.append('project_id', filters.project_id)
    if (filters?.limit) params.append('limit', String(filters.limit))
    if (filters?.offset) params.append('offset', String(filters.offset))

    const query = params.toString()
    return (await this.request<TaskListResponse>(
      'GET',
      `/api/v1/tasks${query ? '?' + query : ''}`
    ))!
  }

  async getTask(taskId: string): Promise<Task> {
    return (await this.request<Task>('GET', `/api/v1/tasks/${taskId}`))!
  }

  async createTask(request: CreateTaskRequest): Promise<Task> {
    return (await this.request<Task>('POST', '/api/v1/tasks', request))!
  }

  async updateTask(taskId: string, request: UpdateTaskRequest): Promise<Task> {
    return (await this.request<Task>('PATCH', `/api/v1/tasks/${taskId}`, request))!
  }

  async claimTask(request: ClaimTaskRequest): Promise<Task | null> {
    return await this.request<Task>('POST', '/api/v1/tasks/claim', request)
  }

  async submitCompletion(
    taskId: string,
    request: SubmitTaskRequest
  ): Promise<Task> {
    return (await this.request<Task>(
      'POST',
      `/api/v1/tasks/${taskId}/submit`,
      request
    ))!
  }

  async acceptCompletion(
    taskId: string,
    request: AcceptTaskRequest
  ): Promise<Task> {
    return (await this.request<Task>(
      'POST',
      `/api/v1/tasks/${taskId}/accept`,
      request
    ))!
  }

  async rejectCompletion(
    taskId: string,
    request: RejectTaskRequest
  ): Promise<Task> {
    return (await this.request<Task>(
      'POST',
      `/api/v1/tasks/${taskId}/reject`,
      request
    ))!
  }
}
