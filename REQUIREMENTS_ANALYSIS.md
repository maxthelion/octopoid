# Octopoid v2.0 Requirements Analysis

**Date**: 2026-02-11
**Status**: Complete Implementation Review
**Source**: Comprehensive requirements document from customer

---

## Executive Summary

Octopoid v2.0 has **successfully implemented the core task orchestration architecture** but is **missing several critical workflow features** described in the requirements document. The foundational infrastructure is solid, but key user-facing functionality needs implementation.

**Overall Completion**: ~60% of requirements implemented

---

## 1. Drafts System

### Requirements
- Lifecycle tracking: `idea` → `draft` → `review` → `approved` → `implemented` → `archived`
- File-based storage: `drafts/{id}.md` with YAML frontmatter
- Conversion to tasks/projects
- Author attribution and tagging
- Integration with task creation workflow

### Implementation Status: ⚠️ **Partial**

✅ **What Exists:**
- Database schema: `drafts` table with all required fields (migrations/0001_initial.sql:94-111)
- TypeScript types: `Draft`, `DraftStatus`, CRUD request types (packages/shared/src/draft.ts)
- Status values: `idea | draft | review | approved | implemented | archived`

❌ **What's Missing:**
- No API routes for drafts (`/api/v1/drafts/*`)
- No client commands for draft management (`octopoid draft create`, etc.)
- No file-based storage system (`drafts/{id}.md`)
- No conversion workflow (draft → task/project)
- No registration script integration

**Priority**: **P0 - Critical for MVP**

**Location**:
- Schema: `packages/server/migrations/0001_initial.sql:94-111`
- Types: `packages/shared/src/draft.ts`
- Missing: `packages/server/src/routes/drafts.ts` (doesn't exist)
- Missing: `packages/client/src/commands/draft.ts` (doesn't exist)

---

## 2. Projects System

### Requirements
- Multi-task containers with shared context
- Foreign key relationships: `tasks.project_id → projects.id`
- Lifecycle: `draft` → `active` → `completed` → `archived`
- Auto-accept flag (bypass gatekeeper for all project tasks)
- Base branch management

### Implementation Status: ⚠️ **Partial**

✅ **What Exists:**
- Database schema: `projects` table with all required fields (migrations/0001_initial.sql:53-65)
- TypeScript types: `Project`, `ProjectStatus`, CRUD request types (packages/shared/src/project.ts)
- Foreign key constraint: `tasks.project_id REFERENCES projects(id)`

❌ **What's Missing:**
- No API routes for projects (`/api/v1/projects/*`)
- No client commands for project management
- No project-based task filtering
- No auto-accept enforcement logic
- No shared branch management

**Priority**: **P1 - Important for team workflows**

**Location**:
- Schema: `packages/server/migrations/0001_initial.sql:53-65`
- Types: `packages/shared/src/project.ts`
- Missing: `packages/server/src/routes/projects.ts` (doesn't exist)

---

## 3. Task Management (Core)

### Requirements
- Full REST API for task operations
- State machine with guards and side effects
- Lease-based claiming with auto-expiration
- Dependency tracking: `tasks.blocked_by`
- Queue management: `incoming`, `claimed`, `provisional`, `done`

### Implementation Status: ✅ **Fully Implemented**

✅ **What Exists:**
- Complete REST API: `packages/server/src/routes/tasks.ts` (418 lines)
- State machine: `packages/server/src/state-machine.ts` (377 lines)
- Lease management: Auto-expire via cron job (packages/server/src/index.ts:116-138)
- Dependency tracking: `blocked_by` field in schema
- Queue filtering: All queue types supported
- Optimistic locking: `version` field for race condition prevention

**Priority**: ✅ **Complete**

---

## 4. Breakdown Agent

### Requirements
- Decomposes complex tasks into subtasks
- Input queue: Tasks marked with `needs_breakdown=true`
- Output: Multiple smaller tasks with dependencies
- Criteria: Tasks > 8 hours estimate OR > 500 lines changed
- Breakdown patterns: horizontal (parallel subtasks), vertical (sequential pipeline)

### Implementation Status: ⚠️ **Partial**

✅ **What Exists:**
- Agent class: `packages/client/src/roles/breakdown.ts` (205 lines)
- Role registered: Available in `getAgentByRole('breakdown')`

❌ **What's Missing:**
- No `needs_breakdown` field in schema
- No automatic task analysis (size estimation)
- No breakdown queue processing logic
- No subtask dependency creation
- Breakdown agent implementation is minimal (stub)

**Priority**: **P1 - Important for complex features**

**Location**:
- Stub implementation: `packages/client/src/roles/breakdown.ts`
- Missing field: `tasks.needs_breakdown` (not in schema)

---

## 5. Gatekeeper Agent

### Requirements
- Reviews completed work before merging
- Multi-check workflow: Up to 3 review rounds
- Rejection handling: Send back to agent for fixes
- Auto-accept bypass: For trusted tasks
- Integration with PR checks

### Implementation Status: ⚠️ **Partial**

✅ **What Exists:**
- Agent class: `packages/client/src/roles/gatekeeper.ts` (225 lines)
- Single review workflow: Claims provisional tasks, calls Claude Opus for review
- Accept/reject operations: Via `acceptTask()` / `rejectTask()`
- Auto-accept field: `tasks.auto_accept` exists in schema

❌ **What's Missing:**
- Multi-check workflow (only single review pass)
- Round counting: No `review_round` field
- Retry logic: No automatic re-submission
- PR check integration: No CI/CD status monitoring

**Priority**: **P2 - Nice to have for quality gates**

**Location**:
- Implementation: `packages/client/src/roles/gatekeeper.ts:23-119`
- Missing: Multi-round review logic

---

## 6. Git Worktrees

### Requirements
- **Task-specific worktrees**: One worktree per task ID (not per agent)
- Path pattern: `.octopoid/worktrees/{task_id}/`
- Parallel execution: Multiple agents can work different tasks simultaneously
- Cleanup: Remove worktree when task completes
- Branch naming: `agent/{task_id}-{timestamp}`

### Implementation Status: ❌ **Incorrect Implementation**

✅ **What Exists:**
- Worktree management: `packages/client/src/git-utils.ts` (599 lines)
- Worktree creation: `ensureWorktree()` function
- Branch creation: `createFeatureBranch()`
- PR creation: `createPullRequest()`

❌ **Critical Problem:**
- **Worktrees are agent-specific, NOT task-specific**
- Implementation: `getWorktreePath(agentName: string)` (git-utils.ts:73)
- Should be: `getWorktreePath(taskId: string)`
- Path: `.octopoid/worktrees/{agentName}` (WRONG)
- Should be: `.octopoid/worktrees/{taskId}` (CORRECT)

**Why This Matters:**
- Current: One agent can only work one task at a time
- Required: Multiple agents can work multiple tasks in parallel
- Current: Agent worktree reused across different tasks (contamination risk)
- Required: Fresh worktree per task (isolation)

**Priority**: **P0 - Critical bug, breaks parallel execution**

**Location**:
- Bug: `packages/client/src/git-utils.ts:73-78`
- Fix needed: Change parameter from `agentName` to `taskId`

---

## 7. Logging and Observability

### Requirements
- **Per-task logs**: `logs/tasks/{task_id}.log` - all work for that task
- **Per-agent logs**: `logs/agents/{agent_name}-{date}.log` - agent activity
- **Scheduler logs**: `logs/scheduler-{date}.log`
- **Turn counting**: Auto-track Claude API calls per task
- **Agent notes**: Execution summary written to task file

### Implementation Status: ⚠️ **Partial**

✅ **What Exists:**
- Per-agent logs: `logs/agents/{agent-name}-{date}.log` (base-agent.ts:56-61)
- Scheduler logs: `logs/scheduler-{date}.log` (scheduler.ts:46-52)
- Turn counting field: `tasks.turns_used` in schema

❌ **What's Missing:**
- **Per-task logs**: No centralized log file per task ID
- **Auto turn counting**: Agents must manually report turns (base-agent.ts:146)
- **Agent notes**: No `execution_notes` field in schema
- **Turn tracking**: No automatic Claude API call counting

**Priority**: **P1 - Important for debugging and cost tracking**

**Location**:
- Per-agent logs: `packages/client/src/roles/base-agent.ts:54-77`
- Missing: Per-task log aggregation
- Turn counting: Manual in `submitTaskCompletion()` (base-agent.ts:143-161)

---

## 8. Burnout Detection

### Requirements
- Heuristic: 0 commits + ≥80 turns used = agent is stuck
- Action: Automatically move task to `needs_continuation` queue
- Human intervention: User reviews and redirects or cancels
- Turn limit: Max 100 turns per task attempt

### Implementation Status: ❌ **Not Implemented**

❌ **What's Missing:**
- No burnout detection logic
- No `needs_continuation` queue (not in schema)
- No automatic task routing
- No turn limit enforcement
- No stuckness heuristic

**Priority**: **P2 - Prevents wasted API costs**

**Location**:
- Missing: Burnout detection logic (should be in scheduler or state machine)
- Missing: `needs_continuation` queue type

---

## 9. Slash Commands (User Interface)

### Requirements
- `/octo:approve {task_id}` - Human approval
- `/octo:reject {task_id} "reason"` - Manual rejection
- `/octo:requeue {task_id}` - Send back to incoming
- `/octo:breakdown {task_id}` - Manually trigger breakdown
- `/octo:status` - Show orchestrator health
- Integration with Claude Code CLI

### Implementation Status: ❌ **Not Implemented**

✅ **What Exists:**
- CLI framework: `packages/client/src/cli.ts` with Commander.js
- Basic commands: `init`, `start`, `stop`, `enqueue`

❌ **What's Missing:**
- All `/octo:*` commands
- Interactive approval workflow
- Manual task operations
- Status dashboard

**Priority**: **P2 - UX improvement**

**Location**:
- CLI: `packages/client/src/cli.ts`
- Missing: Task management commands

---

## 10. CLAUDE Configuration

### Requirements
- Auto-generate `claude-interactive-role.md` based on agent role
- Role-specific prompts for implementer, gatekeeper, breakdown
- Integration with Claude Code CLI via `--role` flag
- Dynamic prompt generation from task context

### Implementation Status: ❌ **Not Implemented**

❌ **What's Missing:**
- No `claude-interactive-role.md` generation
- No role-specific prompt templates
- No dynamic prompt system
- Agents use hardcoded prompts in code

**Priority**: **P3 - Nice to have for customization**

**Location**:
- Missing: Prompt template system

---

## 11. Migration Priority (If Converting from v1.x)

### Requirements Document Priority Order:
1. **Drafts** - P0
2. **Projects + drafts_to_tasks.py** - P0
3. **Tasks schema** - P0
4. **Breakdown agent** - P1
5. **Gatekeeper multi-check** - P1
6. **Worktrees (task-specific)** - P0
7. **Logging separation** - P1
8. **Turn counting** - P1
9. **Burnout detection** - P2
10. **Slash commands** - P2
11. **CLAUDE config** - P3

### Actual v2.0 Implementation Order:
1. ✅ Tasks schema and API - **Complete**
2. ✅ State machine - **Complete**
3. ✅ Orchestrator registration - **Complete**
4. ✅ Lease-based claiming - **Complete**
5. ⚠️ Worktrees - **Implemented incorrectly (agent-based not task-based)**
6. ⚠️ Gatekeeper - **Single-check only**
7. ⚠️ Breakdown agent - **Stub only**
8. ⚠️ Drafts - **Schema only, no API**
9. ⚠️ Projects - **Schema only, no API**
10. ⚠️ Logging - **Per-agent only, no per-task**
11. ⚠️ Turn counting - **Manual only**
12. ❌ Burnout detection - **Not started**
13. ❌ Slash commands - **Not started**
14. ❌ CLAUDE config - **Not started**

---

## Critical Gaps Summary

### P0 - Blocking Issues (Must Fix)
1. **Worktrees are agent-specific, not task-specific** - Breaks parallel execution
2. **Drafts API missing** - Cannot create/manage drafts
3. **Projects API missing** - Cannot organize multi-task features

### P1 - Important Features (Should Fix Soon)
4. **Breakdown agent incomplete** - Cannot decompose complex tasks
5. **Gatekeeper single-check only** - No multi-round review
6. **Per-task logging missing** - Debugging is difficult
7. **Auto turn counting missing** - Manual tracking error-prone

### P2 - Nice to Have (Can Wait)
8. **Burnout detection missing** - Wastes API costs on stuck tasks
9. **Slash commands missing** - CLI needs manual task operations
10. **needs_continuation queue missing** - No human escalation path

### P3 - Polish (Future)
11. **CLAUDE config generation** - Hardcoded prompts work for now

---

## Recommended Next Steps

### Phase 1: Fix Critical Bugs (1 week)
1. **Fix worktree implementation**: Change from agent-based to task-based
   - Modify `git-utils.ts:getWorktreePath()` signature
   - Update all callers to pass `taskId` instead of `agentName`
   - Test parallel task execution

### Phase 2: Complete Core Features (2-3 weeks)
2. **Implement Drafts API**:
   - Create `packages/server/src/routes/drafts.ts`
   - Add CRUD endpoints
   - Create `octopoid draft` CLI commands
   - Add file-based storage

3. **Implement Projects API**:
   - Create `packages/server/src/routes/projects.ts`
   - Add CRUD endpoints
   - Enforce foreign key relationships
   - Add project-based task filtering

4. **Complete Breakdown Agent**:
   - Add `needs_breakdown` field to schema
   - Implement task size estimation
   - Add breakdown queue processing
   - Create subtask with dependencies

### Phase 3: Enhance Quality (2-3 weeks)
5. **Add per-task logging**:
   - Create `logs/tasks/{task_id}.log`
   - Aggregate agent logs by task
   - Add to task lifecycle

6. **Implement auto turn counting**:
   - Wrap Anthropic API calls with counter
   - Auto-increment on each call
   - Report in `submitCompletion()`

7. **Add gatekeeper multi-check**:
   - Add `review_round` field
   - Implement retry logic
   - Cap at 3 rounds

### Phase 4: User Experience (1-2 weeks)
8. **Add slash commands**:
   - `/octo:approve`, `/octo:reject`, etc.
   - Interactive CLI workflows

9. **Add burnout detection**:
   - Check 0 commits + ≥80 turns
   - Route to `needs_continuation` queue
   - Alert human

---

## Files to Modify

### Critical Fixes
- `packages/client/src/git-utils.ts` - Fix worktree path logic
- `packages/client/src/roles/base-agent.ts` - Update worktree calls
- `packages/client/src/roles/implementer.ts` - Update worktree calls

### New Files Needed
- `packages/server/src/routes/drafts.ts` - Drafts API
- `packages/server/src/routes/projects.ts` - Projects API
- `packages/client/src/commands/draft.ts` - Draft CLI
- `packages/client/src/commands/project.ts` - Project CLI

### Schema Changes
- Add migration: `needs_breakdown BOOLEAN`, `review_round INTEGER`, `execution_notes TEXT`
- Add queue type: `needs_continuation`

---

## Conclusion

Octopoid v2.0 has **excellent foundational architecture** (state machine, leases, API) but is **missing critical workflow features**. The core orchestration works, but key user-facing functionality (drafts, projects, proper worktrees, logging) needs implementation.

**Good News**: The hardest parts are done (distributed coordination, state machine)
**Work Remaining**: ~40% feature completeness, mostly CRUD APIs and workflow logic

**Recommendation**: Fix the worktree bug (P0), then focus on completing drafts/projects APIs (P0/P1) before adding polish features (P2/P3).
