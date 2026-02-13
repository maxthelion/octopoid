/**
 * Debug and observability types for Octopoid
 * These endpoints provide visibility into task states, queue health, and agent activity
 */

import type { TaskQueue } from './task.js'

/**
 * Task-level debug information
 * GET /api/v1/tasks/:id/debug
 */
export interface TaskDebugInfo {
  task_id: string
  state: TaskQueue
  lease_expires_in?: string | null  // Human-readable duration (e.g., "14m 32s")
  lease_expires_at?: string | null  // ISO timestamp
  blocking: {
    is_blocked: boolean
    blocked_by: string | null
    blocks: string[]  // Task IDs that are blocked by this task
  }
  burnout: {
    is_burned_out: boolean
    turns_used: number
    commits_count: number
    threshold: number
  }
  gatekeeper: {
    review_round: number
    max_rounds: number
    rejection_count: number
  }
  attempts: {
    attempt_count: number
    last_claimed_at?: string | null
    last_submitted_at?: string | null
  }
}

/**
 * Queue-level debug information
 * GET /api/v1/debug/queues
 */
export interface QueueDebugInfo {
  queues: {
    [queue in TaskQueue]?: {
      count: number
      oldest_task?: {
        id: string
        age: string  // Human-readable duration (e.g., "2h 14m")
        created_at: string  // ISO timestamp
      } | null
    }
  }
  claimed: {
    count: number
    tasks: Array<{
      id: string
      claimed_by: string
      orchestrator_id: string
      claimed_for: string  // Human-readable duration (e.g., "8m 32s")
      lease_expires_in: string  // Human-readable duration (e.g., "21m 28s")
      lease_expires_at: string  // ISO timestamp
    }>
  }
}

/**
 * Agent activity information
 */
export interface AgentActivity {
  orchestrator_id: string
  agent_name: string
  role: string
  current_task?: {
    id: string
    claimed_at: string
    lease_expires_at: string
  } | null
  stats: {
    tasks_claimed: number
    tasks_completed: number
    tasks_failed: number
    success_rate: number  // 0.0 to 1.0
  }
  last_active_at?: string | null
}

/**
 * Orchestrator health information
 */
export interface OrchestratorHealth {
  orchestrator_id: string
  cluster: string
  machine_id: string
  status: 'active' | 'idle' | 'offline'
  last_heartbeat_at?: string | null
  heartbeat_age?: string | null  // Human-readable duration since last heartbeat
  current_tasks: number
  total_completed: number
  total_failed: number
}

/**
 * Agent-level debug information
 * GET /api/v1/debug/agents
 */
export interface AgentDebugInfo {
  orchestrators: OrchestratorHealth[]
  agents: AgentActivity[]
  summary: {
    total_orchestrators: number
    active_orchestrators: number
    total_agents: number
    total_claimed_tasks: number
  }
}

/**
 * Comprehensive status overview
 * GET /api/v1/debug/status
 */
export interface SystemStatusInfo {
  timestamp: string
  queues: QueueDebugInfo
  agents: AgentDebugInfo
  health: {
    oldest_incoming_task?: {
      id: string
      age: string
      created_at: string
    } | null
    stuck_tasks: Array<{
      id: string
      queue: TaskQueue
      issue: string  // Description of why it's stuck
      claimed_at?: string | null
      lease_expires_at?: string | null
    }>
    zombie_claims: Array<{
      id: string
      claimed_by: string
      orchestrator_id: string
      lease_expired: boolean
      lease_expires_at: string
    }>
  }
  metrics: {
    avg_time_to_claim: string | null  // Human-readable duration
    avg_time_to_complete: string | null  // Human-readable duration
    tasks_created_24h: number
    tasks_completed_24h: number
    tasks_failed_24h: number
  }
}
