# Remaining Implementation Tasks

This document outlines the tasks that still need to be completed for the Octopoid v2.0 rewrite.

## Completed Tasks ✅

- [x] #1: Monorepo structure with pnpm workspaces
- [x] #2: Shared types package (@octopoid/shared)
- [x] #3: Server package foundation
- [x] #4: State machine for task transitions
- [x] #5: Server API routes for tasks
- [x] #6: Server API routes for orchestrators
- [x] #7: Scheduled jobs for lease monitoring
- [x] #8: Client package foundation
- [x] #13: CLI commands (init, status, enqueue, list)
- [x] #17: Migration tools and guide
- [x] #18: Comprehensive documentation
- [x] #21: Deployment configuration

## Remaining Tasks 🚧

### Task #9: Port Scheduler from Python to TypeScript

**Files to port:**
- `orchestrator/scheduler.py` (1,972 lines)

**What needs to be implemented:**

1. **Main scheduler loop** (`packages/client/src/scheduler.ts`):
   - Tick interval management (60 seconds)
   - Agent spawning based on agents.yaml
   - Task claiming for agents
   - Agent lifecycle management (spawn, monitor, cleanup)
   - Graceful shutdown handling

2. **Orchestrator registration:**
   - Register with server on startup
   - Periodic heartbeat (every 30 seconds)
   - Store orchestrator_id locally

3. **Runtime management:**
   - PID file creation/removal
   - Lock files for synchronization
   - Process monitoring

**Key functions to implement:**
```typescript
export async function runScheduler(options: {
  once?: boolean
  daemon?: boolean
}): Promise<void>

async function registerOrchestrator(): Promise<string>
async function sendHeartbeat(): Promise<void>
async function spawnAgentsFromConfig(): Promise<void>
async function monitorAgents(): Promise<void>
async function gracefulShutdown(): Promise<void>
```

**Dependencies:**
- Agent role implementations (#12)
- Git utilities (#11)
- Queue utilities (#10)

**Estimated effort:** 2-3 weeks

---

### Task #10: Port Queue Utilities from Python to TypeScript

**Files to port:**
- `orchestrator/queue_utils.py` (2,003 lines)

**What needs to be implemented:**

1. **Task file operations** (`packages/client/src/queue-utils.ts`):
   - Read task markdown files
   - Parse frontmatter (YAML) + body
   - Write/update task files
   - Move tasks between queues (directories)

2. **Queue directory management:**
   - incoming/, claimed/, provisional/, done/, blocked/
   - File listing and filtering
   - Task ID generation

3. **File parsing:**
   - YAML frontmatter extraction
   - Markdown body parsing
   - Validation

**Key functions to implement:**
```typescript
export interface TaskFile {
  id: string
  filePath: string
  frontmatter: Record<string, any>
  body: string
}

export async function readTaskFile(filePath: string): Promise<TaskFile>
export async function writeTaskFile(task: TaskFile): Promise<void>
export async function moveTaskFile(
  taskId: string,
  fromQueue: string,
  toQueue: string
): Promise<void>
export async function listTaskFiles(queue: string): Promise<TaskFile[]>
export function generateTaskId(): string
```

**Dependencies:**
- Node.js fs/promises
- YAML parser (already in package.json)
- Path manipulation

**Estimated effort:** 1-2 weeks

---

### Task #11: Port Git Utilities from Python to TypeScript

**Files to port:**
- `orchestrator/git_utils.py` (619 lines)

**What needs to be implemented:**

1. **Git worktree operations** (`packages/client/src/git-utils.ts`):
   - Create worktree for agent
   - Remove worktree after completion
   - List active worktrees

2. **Branch management:**
   - Create branch for task
   - Checkout branch
   - Merge branches
   - Delete branches

3. **Commit operations:**
   - Stage files
   - Create commits
   - Push to remote

4. **Repository info:**
   - Get current branch
   - Check if clean/dirty
   - Get remote URL

**Key functions to implement:**
```typescript
export interface GitWorktree {
  path: string
  branch: string
  commit: string
}

export async function createWorktree(
  repoPath: string,
  branch: string,
  worktreePath: string
): Promise<GitWorktree>

export async function removeWorktree(worktreePath: string): Promise<void>
export async function commit(
  worktreePath: string,
  message: string,
  files?: string[]
): Promise<string>
export async function push(
  worktreePath: string,
  remote: string,
  branch: string
): Promise<void>
export async function createBranch(
  repoPath: string,
  branchName: string,
  baseBranch?: string
): Promise<void>
```

**Dependencies:**
- simple-git library (already in package.json)

**Estimated effort:** 1 week

---

### Task #12: Port Core Agent Roles to TypeScript

**Files to port:**
- `orchestrator/roles/breakdown.py` (~400 lines)
- `orchestrator/roles/gatekeeper.py` (~450 lines)
- `orchestrator/roles/tester.py` (~350 lines)
- `orchestrator/roles/reviewer.py` (~300 lines)
- `orchestrator/roles/base_agent.py` (~200 lines)

> **Note:** `implementer.py` / `ImplementerRole` has been removed. The implementer now uses
> scripts mode only (`prepare_task_directory()` + `invoke_claude()` in `scheduler.py`).
> `OrchestratorImplRole` inherits from `BaseRole` directly.

**What needs to be implemented:**

1. **Base Agent class** (`packages/client/src/roles/base-agent.ts`):
   - Common agent functionality
   - Anthropic API integration
   - Task claiming
   - Git worktree management
   - Completion submission

2. **Implementer (scripts mode):**
   - Handled by `prepare_task_directory()` + `invoke_claude()` in scheduler
   - No separate agent class needed; scheduler sets up worktree and spawns Claude directly

3. **Breakdown agent** (`packages/client/src/roles/breakdown.ts`):
   - Claim tasks with role='breakdown'
   - Analyze large tasks
   - Create subtasks
   - Update task dependencies

4. **Gatekeeper agent** (`packages/client/src/roles/gatekeeper.ts`):
   - Claim tasks in 'provisional' queue
   - Review implementations
   - Run checks/tests
   - Accept or reject

5. **Test/Review agents:**
   - Similar patterns to above

**Key classes to implement:**
```typescript
export abstract class BaseAgent {
  constructor(
    protected name: string,
    protected role: string,
    protected config: AgentConfig
  )

  abstract async run(): Promise<void>

  protected async claimTask(): Promise<Task | null>
  protected async submitCompletion(task: Task): Promise<void>
  protected async callAnthropic(prompt: string): Promise<string>
}
```

**Dependencies:**
- @anthropic-ai/sdk (already in package.json)
- Git utilities (#11)
- Queue utilities (#10)

**Estimated effort:** 3-4 weeks

---

### Task #14: Implement Offline Mode with Local Cache

**What needs to be implemented:**

1. **Local cache database** (`packages/client/src/local-cache.ts`):
   - SQLite database at `.octopoid/cache.db`
   - Same schema as server D1
   - Implements same interface as API client

2. **Sync manager** (`packages/client/src/sync-manager.ts`):
   - Queue operations for sync when offline
   - Background sync process
   - Conflict resolution (server wins)

3. **Smart fallback** (update `packages/client/src/db-interface.ts`):
   - Try server first
   - Fall back to local cache on network error
   - Queue for sync when server returns

**Key functions to implement:**
```typescript
export class LocalCache {
  constructor(dbPath: string)

  async createTask(request: CreateTaskRequest): Promise<Task>
  async getTask(taskId: string): Promise<Task | null>
  async listTasks(filters: TaskFilters): Promise<Task[]>
  // ... all operations
}

export class SyncManager {
  constructor(
    private localCache: LocalCache,
    private apiClient: OctopoidAPIClient
  )

  async sync(): Promise<SyncResult>
  async queueOperation(op: Operation): Promise<void>
  async getPendingCount(): Promise<number>
}
```

**Dependencies:**
- better-sqlite3 (already in package.json)

**Estimated effort:** 1-2 weeks

---

### Task #15: Create Python SDK for Custom Scripts

**Create new package:** `octopoid-sdk-python` (separate repo or packages/sdk-python/)

**What needs to be implemented:**

1. **OctopoidClient class:**
```python
from octopoid_sdk import OctopoidClient

client = OctopoidClient(
    server_url="https://octopoid.example.com",
    api_key=os.getenv("OCTOPOID_API_KEY")
)

# Task operations
tasks = client.list_tasks(queue='incoming', priority='P1')
task = client.get_task('task-123')
client.create_task(id='new-123', file_path='tasks/new-123.md', ...)
client.update_task('task-123', priority='P0')
client.claim_task(orchestrator_id='prod-001', agent_name='agent-1')
client.submit_completion('task-123', commits_count=5, turns_used=10)
client.accept_completion('task-123', accepted_by='reviewer')
client.reject_completion('task-123', reason='Failed tests')

# Project operations
client.create_project(...)
client.get_project('proj-123')
client.list_projects()

# Orchestrator operations
client.register_orchestrator(...)
client.send_heartbeat(orchestrator_id='prod-001')
```

2. **Type hints:**
```python
from typing import TypedDict, Optional, List

class Task(TypedDict):
    id: str
    file_path: str
    queue: str
    priority: str
    # ... all fields

class OctopoidClient:
    def list_tasks(
        self,
        queue: Optional[str] = None,
        priority: Optional[str] = None,
        role: Optional[str] = None,
        limit: int = 50
    ) -> List[Task]:
        ...
```

3. **Setup:**
```bash
# pyproject.toml or setup.py
pip install octopoid-sdk
```

**Dependencies:**
- requests (HTTP client)
- typing-extensions (for TypedDict)

**Estimated effort:** 1 week

---

### Task #16: Create Node.js SDK for Custom Scripts

**Package:** `@octopoid/sdk` or export from `octopoid`

**What needs to be implemented:**

Already mostly done via `packages/client/src/api-client.ts`. Just need to:

1. Export SDK from client package:
```typescript
// packages/client/src/sdk.ts
export { OctopoidAPIClient as OctopoidClient } from './api-client'
export type * from '@octopoid/shared'
```

2. Add to package.json exports:
```json
{
  "exports": {
    ".": "./dist/index.js",
    "./sdk": "./dist/sdk.js"
  }
}
```

3. Documentation and examples.

**Estimated effort:** 2-3 days

---

### Task #19: Create Test Suite for Server

**What needs to be implemented:**

1. **Unit tests** (`packages/server/tests/`):
   - State machine transitions
   - Guard evaluations
   - Side effect execution

2. **Integration tests:**
   - API endpoint testing
   - Database operations
   - Lease expiration

3. **Test utilities:**
   - Mock D1 database
   - Test fixtures
   - Helper functions

**Example:**
```typescript
// packages/server/tests/state-machine.test.ts
import { describe, it, expect } from 'vitest'
import { executeTransition, TRANSITIONS } from '../src/state-machine'

describe('State machine', () => {
  it('should allow claiming incoming tasks', async () => {
    // Setup mock DB and task
    // Execute transition
    // Verify result
  })

  it('should reject claiming blocked tasks', async () => {
    // ...
  })
})
```

**Framework:** Vitest (already in package.json)

**Estimated effort:** 1-2 weeks

---

### Task #20: Create Test Suite for Client

**What needs to be implemented:**

1. **Unit tests** (`packages/client/tests/`):
   - API client operations
   - Queue utilities
   - Git utilities
   - Configuration loading

2. **Integration tests:**
   - CLI commands
   - Agent lifecycle
   - Scheduler operations

3. **E2E tests:**
   - Full workflow (create → claim → submit → accept)

**Framework:** Vitest (already in package.json)

**Estimated effort:** 1-2 weeks

---

## Priority Recommendations

### Phase 1: Core Functionality (High Priority)
1. **Task #10:** Queue utilities (needed by agents)
2. **Task #11:** Git utilities (needed by agents)
3. **Task #12:** Agent roles (core functionality)
4. **Task #9:** Scheduler (ties everything together)

**Timeline:** 7-10 weeks

### Phase 2: SDK & Extensions (Medium Priority)
5. **Task #15:** Python SDK (for custom scripts)
6. **Task #16:** Node.js SDK (easy, builds on existing)
7. **Task #14:** Offline mode (nice-to-have)

**Timeline:** 2-3 weeks

### Phase 3: Testing & Quality (Lower Priority but Important)
8. **Task #19:** Server tests
9. **Task #20:** Client tests

**Timeline:** 2-4 weeks

## Total Estimated Timeline

**Phase 1 only:** 7-10 weeks
**All phases:** 11-17 weeks

## How to Approach

### For Each Task:

1. **Read Python code** to understand logic
2. **Create TypeScript interface** matching functionality
3. **Port logic piece by piece**, testing as you go
4. **Add error handling** and edge cases
5. **Write tests** (or at least manual verification)
6. **Document** any changes from Python version

### Tools to Help:

- Use GPT-4/Claude to assist with porting
- Keep Python version as reference
- Test incrementally, don't port everything at once
- Use TypeScript's type system to catch errors early

### Code Quality:

- Follow existing TypeScript conventions
- Use async/await consistently
- Add JSDoc comments for public APIs
- Handle errors gracefully
- Log important operations

## Status Dashboard

Visit `/docs/PROGRESS.md` (to be created) for real-time status updates on each task.

## Questions?

For implementation questions or architectural decisions, create an issue in the repository or consult the architecture documentation at `/docs/architecture-v2.md`.
