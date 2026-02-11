/**
 * Draft types for Octopoid
 * Based on orchestrator/db.py schema
 */

export type DraftStatus = 'idea' | 'draft' | 'review' | 'approved' | 'implemented' | 'archived'

export interface Draft {
  id: string
  title: string
  status: DraftStatus
  author: string
  domain?: string | null
  file_path?: string | null
  created_at: string
  updated_at: string
  linked_task_id?: string | null
  linked_project_id?: string | null
  tags?: string | null  // JSON string array
}

export interface CreateDraftRequest {
  id: string
  title: string
  status?: DraftStatus
  author: string
  domain?: string
  file_path?: string
  linked_task_id?: string
  linked_project_id?: string
  tags?: string[]
}

export interface UpdateDraftRequest {
  title?: string
  status?: DraftStatus
  author?: string
  domain?: string
  file_path?: string
  updated_at?: string
  linked_task_id?: string
  linked_project_id?: string
  tags?: string[]
}

export interface DraftFilters {
  status?: DraftStatus | DraftStatus[]
  author?: string
  domain?: string
  linked_task_id?: string
  linked_project_id?: string
}

export interface DraftListResponse {
  drafts: Draft[]
  total: number
  offset: number
  limit: number
}
