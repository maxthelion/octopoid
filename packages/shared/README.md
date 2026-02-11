# @octopoid/shared

Shared TypeScript types for Octopoid v2.0 client-server architecture.

## Overview

This package contains all shared type definitions used by both the Octopoid server (@octopoid/server) and client (octopoid) packages. It ensures type consistency across the distributed system.

## Installation

```bash
pnpm add @octopoid/shared
```

## Usage

```typescript
import type {
  Task,
  TaskQueue,
  CreateTaskRequest,
  Orchestrator,
  Project,
} from '@octopoid/shared'

// Use types in your code
const task: Task = {
  id: 'task-123',
  file_path: 'tasks/task-123.md',
  queue: 'incoming',
  priority: 'P1',
  // ... other fields
}
```

## Type Categories

### Task Types
- `Task` - Complete task object
- `TaskQueue`, `TaskPriority`, `TaskComplexity`, `TaskRole` - Enums
- `CreateTaskRequest`, `UpdateTaskRequest` - Request types
- `ClaimTaskRequest`, `SubmitTaskRequest`, `AcceptTaskRequest`, `RejectTaskRequest` - Lifecycle operations
- `TaskFilters`, `TaskListResponse` - Query types

### Project Types
- `Project` - Project container for multi-task features
- `ProjectStatus` - Project lifecycle states
- `CreateProjectRequest`, `UpdateProjectRequest` - Request types
- `ProjectFilters`, `ProjectListResponse` - Query types

### Agent Types
- `Agent` - Runtime agent state
- `CreateAgentRequest`, `UpdateAgentRequest` - Request types
- `AgentListResponse` - Query types

### Orchestrator Types (v2.0)
- `Orchestrator` - Client orchestrator registration
- `OrchestratorStatus`, `OrchestratorCapabilities` - Metadata
- `RegisterOrchestratorRequest`, `HeartbeatRequest` - Client operations
- `OrchestratorFilters`, `OrchestratorListResponse` - Query types

### History Types
- `TaskHistory` - Audit trail entries
- `TaskEvent` - Event types
- `CreateTaskHistoryRequest` - Request types
- `TaskHistoryFilters`, `TaskHistoryListResponse` - Query types

### Draft Types
- `Draft` - Draft document lifecycle
- `DraftStatus` - Draft states
- `CreateDraftRequest`, `UpdateDraftRequest` - Request types
- `DraftFilters`, `DraftListResponse` - Query types

### State Machine Types
- `StateTransition` - Valid state transitions
- `StateTransitionGuard`, `StateTransitionSideEffect` - Transition rules
- `VALID_TRANSITIONS` - Constant map of all valid transitions

### API Types
- `HealthCheckResponse` - System health endpoint
- `ErrorResponse` - Standard error format
- `PaginationParams`, `SortParams` - Query helpers
- `ApiResponse<T>` - Generic response wrapper
- `BatchOperationRequest`, `BatchOperationResponse` - Batch operations

## Development

```bash
# Build types
pnpm build

# Watch for changes
pnpm watch

# Type checking
pnpm typecheck
```

## Versioning

This package follows semantic versioning and is kept in sync with the main Octopoid version.

## License

MIT
