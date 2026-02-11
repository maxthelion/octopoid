/**
 * Task types for Octopoid
 * Based on orchestrator/db.py schema
 */

export type TaskQueue = 'incoming' | 'claimed' | 'provisional' | 'done' | 'backlog' | 'blocked' | 'needs_continuation'

export type TaskPriority = 'P0' | 'P1' | 'P2' | 'P3'

export type TaskComplexity = 'XS' | 'S' | 'M' | 'L' | 'XL'

export type TaskRole = 'implement' | 'breakdown' | 'test' | 'review' | 'fix' | 'research'

export interface Task {
  id: string
  file_path: string
  queue: TaskQueue
  priority: TaskPriority
  complexity?: TaskComplexity | null
  role?: TaskRole | null
  branch: string
  blocked_by?: string | null
  claimed_by?: string | null
  claimed_at?: string | null
  commits_count: number
  turns_used?: number | null
  attempt_count: number
  has_plan: boolean
  plan_id?: string | null
  project_id?: string | null
  auto_accept: boolean
  rejection_count: number
  pr_number?: number | null
  pr_url?: string | null
  checks?: string | null
  check_results?: string | null
  needs_rebase: boolean
  last_rebase_attempt_at?: string | null
  staging_url?: string | null
  submitted_at?: string | null
  completed_at?: string | null
  created_at: string
  updated_at: string

  // Client-server fields (v2.0)
  orchestrator_id?: string | null
  lease_expires_at?: string | null
  version: number  // Optimistic locking

  // Enhanced features
  needs_breakdown?: boolean | null  // For breakdown agent
  review_round?: number | null      // For multi-check gatekeeper
  execution_notes?: string | null   // Agent execution summary
}

export interface CreateTaskRequest {
  id: string
  file_path: string
  title?: string
  queue?: TaskQueue
  priority?: TaskPriority
  complexity?: TaskComplexity
  role?: TaskRole
  branch?: string
  blocked_by?: string
  project_id?: string
  auto_accept?: boolean
}

export interface UpdateTaskRequest {
  queue?: TaskQueue
  priority?: TaskPriority
  complexity?: TaskComplexity
  role?: TaskRole
  branch?: string
  blocked_by?: string
  claimed_by?: string
  claimed_at?: string
  commits_count?: number
  turns_used?: number
  attempt_count?: number
  has_plan?: boolean
  plan_id?: string
  project_id?: string
  auto_accept?: boolean
  rejection_count?: number
  pr_number?: number
  pr_url?: string
  checks?: string
  check_results?: string
  needs_rebase?: boolean
  last_rebase_attempt_at?: string
  staging_url?: string
  submitted_at?: string
  completed_at?: string
  version?: number
}

export interface ClaimTaskRequest {
  orchestrator_id: string
  agent_name: string
  role_filter?: TaskRole | TaskRole[]
  priority_order?: TaskPriority[]
  lease_duration_seconds?: number  // Default: 300 (5 minutes)
}

export interface SubmitTaskRequest {
  commits_count: number
  turns_used: number
  check_results?: string
}

export interface AcceptTaskRequest {
  accepted_by: string
  completed_at?: string
}

export interface RejectTaskRequest {
  reason: string
  rejected_by: string
}

export interface TaskFilters {
  queue?: TaskQueue | TaskQueue[]
  priority?: TaskPriority | TaskPriority[]
  role?: TaskRole | TaskRole[]
  claimed_by?: string
  project_id?: string
  has_plan?: boolean
  auto_accept?: boolean
  needs_rebase?: boolean
}

export interface TaskListResponse {
  tasks: Task[]
  total: number
  offset: number
  limit: number
}
