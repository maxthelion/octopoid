/**
 * Common API types for Octopoid v2.0
 */

export interface HealthCheckResponse {
  status: 'healthy' | 'degraded' | 'unhealthy'
  version: string
  timestamp: string
  database?: 'connected' | 'disconnected'
}

export interface ErrorResponse {
  error: string
  message: string
  details?: Record<string, unknown>
  timestamp: string
}

export interface PaginationParams {
  offset?: number
  limit?: number
}

export interface SortParams {
  sort_by?: string
  sort_order?: 'asc' | 'desc'
}

export interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: ErrorResponse
}

export interface BatchOperationRequest<T> {
  operations: T[]
}

export interface BatchOperationResponse {
  succeeded: number
  failed: number
  errors?: Array<{ index: number; error: string }>
}
