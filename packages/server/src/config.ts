/**
 * Server configuration for Octopoid
 */

export interface ServerConfig {
  // Server info
  version: string
  environment: 'development' | 'staging' | 'production'

  // Task claiming
  defaultLeaseDurationSeconds: number
  maxLeaseDurationSeconds: number

  // Heartbeat
  heartbeatIntervalSeconds: number
  staleOrchestratorTimeoutSeconds: number

  // Pagination
  defaultPageSize: number
  maxPageSize: number

  // Rate limiting (future)
  rateLimit?: {
    enabled: boolean
    requestsPerMinute: number
  }
}

export const DEFAULT_CONFIG: ServerConfig = {
  version: '2.0.0',
  environment: 'production',
  defaultLeaseDurationSeconds: 300, // 5 minutes
  maxLeaseDurationSeconds: 3600, // 1 hour
  heartbeatIntervalSeconds: 30,
  staleOrchestratorTimeoutSeconds: 120, // 2 minutes
  defaultPageSize: 50,
  maxPageSize: 500,
}

/**
 * Get server configuration (can be extended to read from env vars)
 */
export function getConfig(): ServerConfig {
  return DEFAULT_CONFIG
}
