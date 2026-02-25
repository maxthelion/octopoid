# Enforce scope on all server endpoints — disallow NULL scope

## Problem

The server currently allows `scope` to be NULL on both task creation and querying. This caused an orchestrator with no scope configured to claim a task from a completely different project (`scope: boxen`). Tasks and orchestrators without a scope are invisible bugs waiting to happen.

**GH-227 (P1):** The poll endpoint at `GET /api/v1/scheduler/poll` returns unscoped `queue_counts` and `provisional_tasks`. This means an orchestrator for scope `boxen` receives claimed task counts that include scope `octopoid` tasks, hitting the `max_claimed` limit and blocking all boxen tasks from being picked up. Similarly, `GET /api/v1/tasks?scope=X` may not filter by scope, causing `/queue-status` to display tasks from all scopes.

## Requirements

1. **Task creation (`POST /api/v1/tasks`):** reject requests where `scope` is missing or NULL. Return 400 with a clear error message.

2. **Task querying (`GET /api/v1/tasks`):** require `scope` as a query parameter. Never return tasks across all scopes. Return 400 if `scope` is not provided.

3. **Task claiming (`POST /api/v1/tasks/claim`):** require `scope` in the request body. Only return tasks matching the given scope. Return 400 if `scope` is missing.

4. **Orchestrator registration (`POST /api/v1/orchestrators`):** reject registration if `scope` is missing or NULL.

5. **Poll endpoint (`GET /api/v1/scheduler/poll?orchestrator_id=<id>`):** scope-filter all returned data (queue_counts, provisional_tasks, etc.) to the scope provided in the request. This is critical — an unscoped poll response causes cross-scope capacity blocking (GH-227). The orchestrator passes `?scope=<scope>` as a query parameter; the server must use it.

6. **Migration:** any existing tasks or orchestrators with NULL scope should be identified. Consider a one-time migration to assign them a default scope, or leave them orphaned with a warning log.

## Non-goals

- Cross-scope queries (admin use case) — can be added later with an explicit `scope=*` parameter.
- Scope CRUD or management — scopes are implicit strings, not managed entities.

## Context

The octopoid orchestrator registered with `scope=None` because `config.yaml` had no scope field. It then claimed task `65dbf123` (scope: `boxen`) from a different project. The agent ran against the wrong codebase.
