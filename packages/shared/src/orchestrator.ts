/**
 * Orchestrator types for Octopoid v2.0
 * New types for client-server architecture
 */

export type OrchestratorStatus = 'active' | 'offline' | 'maintenance'

export interface OrchestratorCapabilities {
  roles: string[]
  max_agents?: number
  max_concurrent_tasks?: number
  supports_gpu?: boolean
  [key: string]: unknown
}

export interface Orchestrator {
  id: string  // Format: cluster-machine_id
  cluster: string
  machine_id: string
  hostname?: string | null
  repo_url: string
  registered_at: string
  last_heartbeat: string
  status: OrchestratorStatus
  version?: string | null
  capabilities?: OrchestratorCapabilities | null
}

export interface RegisterOrchestratorRequest {
  cluster: string
  machine_id: string
  hostname?: string
  repo_url: string
  version?: string
  capabilities?: OrchestratorCapabilities
}

export interface RegisterOrchestratorResponse {
  orchestrator_id: string
  registered_at: string
  status: OrchestratorStatus
}

export interface HeartbeatRequest {
  timestamp: string
}

export interface HeartbeatResponse {
  success: boolean
  last_heartbeat: string
}

export interface OrchestratorFilters {
  cluster?: string
  status?: OrchestratorStatus | OrchestratorStatus[]
}

export interface OrchestratorListResponse {
  orchestrators: Orchestrator[]
  total: number
}
