/**
 * Shared types for Octopoid v2.0
 * @module @octopoid/shared
 */

// Task types
export type {
  Task,
  TaskQueue,
  TaskPriority,
  TaskComplexity,
  TaskRole,
  CreateTaskRequest,
  UpdateTaskRequest,
  ClaimTaskRequest,
  SubmitTaskRequest,
  AcceptTaskRequest,
  RejectTaskRequest,
  TaskFilters,
  TaskListResponse,
} from './task.js'

// Project types
export type {
  Project,
  ProjectStatus,
  CreateProjectRequest,
  UpdateProjectRequest,
  ProjectFilters,
  ProjectListResponse,
} from './project.js'

// Agent types
export type {
  Agent,
  CreateAgentRequest,
  UpdateAgentRequest,
  AgentListResponse,
} from './agent.js'

// Orchestrator types
export type {
  Orchestrator,
  OrchestratorStatus,
  OrchestratorCapabilities,
  RegisterOrchestratorRequest,
  RegisterOrchestratorResponse,
  HeartbeatRequest,
  HeartbeatResponse,
  OrchestratorFilters,
  OrchestratorListResponse,
} from './orchestrator.js'

// History types
export type {
  TaskHistory,
  TaskEvent,
  CreateTaskHistoryRequest,
  TaskHistoryFilters,
  TaskHistoryListResponse,
} from './history.js'

// Draft types
export type {
  Draft,
  DraftStatus,
  CreateDraftRequest,
  UpdateDraftRequest,
  DraftFilters,
  DraftListResponse,
} from './draft.js'

// State machine types
export type {
  StateTransition,
  StateTransitionGuard,
  StateTransitionSideEffect,
  StateTransitionRequest,
  StateTransitionResponse,
} from './state-machine.js'
export { VALID_TRANSITIONS } from './state-machine.js'

// API types
export type {
  HealthCheckResponse,
  ErrorResponse,
  PaginationParams,
  SortParams,
  ApiResponse,
  BatchOperationRequest,
  BatchOperationResponse,
} from './api.js'
