/**
 * Project types for Octopoid
 * Based on orchestrator/db.py schema
 */

export type ProjectStatus = 'draft' | 'active' | 'completed' | 'archived'

export interface Project {
  id: string
  title: string
  description?: string | null
  status: ProjectStatus
  branch?: string | null
  base_branch: string
  auto_accept: boolean
  created_at: string
  created_by?: string | null
  completed_at?: string | null
}

export interface CreateProjectRequest {
  id: string
  title: string
  description?: string
  status?: ProjectStatus
  branch?: string
  base_branch?: string
  auto_accept?: boolean
  created_by?: string
}

export interface UpdateProjectRequest {
  title?: string
  description?: string
  status?: ProjectStatus
  branch?: string
  base_branch?: string
  auto_accept?: boolean
  completed_at?: string
}

export interface ProjectFilters {
  status?: ProjectStatus | ProjectStatus[]
  created_by?: string
}

export interface ProjectListResponse {
  projects: Project[]
  total: number
  offset: number
  limit: number
}
