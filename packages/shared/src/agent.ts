/**
 * Agent types for Octopoid
 * Based on orchestrator/db.py schema
 */

export interface Agent {
  name: string
  role?: string | null
  running: boolean
  pid?: number | null
  current_task_id?: string | null
  last_run_start?: string | null
  last_run_end?: string | null
}

export interface CreateAgentRequest {
  name: string
  role?: string
}

export interface UpdateAgentRequest {
  role?: string
  running?: boolean
  pid?: number
  current_task_id?: string
  last_run_start?: string
  last_run_end?: string
}

export interface AgentListResponse {
  agents: Agent[]
  total: number
}
