/**
 * Task history types for Octopoid
 * Based on orchestrator/db.py schema
 */

export type TaskEvent =
  | 'created'
  | 'claimed'
  | 'submitted'
  | 'accepted'
  | 'rejected'
  | 'blocked'
  | 'unblocked'
  | 'requeued'
  | 'archived'

export interface TaskHistory {
  id: number
  task_id: string
  event: TaskEvent
  agent?: string | null
  details?: string | null
  timestamp: string
}

export interface CreateTaskHistoryRequest {
  task_id: string
  event: TaskEvent
  agent?: string
  details?: string
}

export interface TaskHistoryFilters {
  task_id?: string
  event?: TaskEvent | TaskEvent[]
  agent?: string
  since?: string  // ISO timestamp
}

export interface TaskHistoryListResponse {
  history: TaskHistory[]
  total: number
}
