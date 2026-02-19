# Server-Registered Roles: No Hardcoded Enums

**Status:** Idea
**Captured:** 2026-02-18

## Raw

> The bigger issue is that we're using strings rather than say enumerables. And these need to match in both repos.
>
> Maybe the answer is different — that the server shouldn't have anything like implementer hard coded at all. That there should be a table of roles that orchestrators can register. When they create tasks, they refer to those roles.

## Problem

Role identifiers are defined as loose strings in three separate places with no shared source of truth:

**Python orchestrator** (`orchestrator/config.py`):
```python
AgentRole = Literal["implementer", "orchestrator_impl", "breakdown", "gatekeeper", ...]
TaskRole = Literal["implement", "orchestrator_impl", "breakdown", "review", "test"]
```

**TypeScript server** (`src/types/shared.ts`):
```typescript
type TaskRole = 'implement' | 'orchestrator_impl' | 'breakdown' | 'review' | 'test'
```

**Agent configs** (`.octopoid/agents/*/agent.yaml`):
```yaml
role: implementer  # matches AgentRole, not TaskRole
```

The agent role (`implementer`) and task role (`implement`) are different strings. The server's claim endpoint does exact string matching. This caused 69 consecutive claim failures — invisible until manual debugging.

Current workaround: `AGENT_TO_TASK_ROLE` mapping dict in `orchestrator/tasks.py`. Fragile.

## Fix: Server Has No Role Opinions

The server is a coordination layer. It shouldn't know or care what roles exist — that's the orchestrator's domain. Roles become data, not code.

### 1. Roles table

```sql
CREATE TABLE roles (
    name TEXT PRIMARY KEY,           -- "implement", "review", etc.
    claims_from TEXT DEFAULT 'incoming',  -- which queue this role claims from
    orchestrator_id TEXT NOT NULL,    -- who registered it
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (orchestrator_id) REFERENCES orchestrators(id)
);
```

### 2. Orchestrator registers roles on startup

Alongside the existing orchestrator registration, register the roles this orchestrator's agents handle:

```python
# In scheduler startup, after _register_orchestrator():
sdk.roles.register([
    {"name": "implement", "claims_from": "incoming"},
    {"name": "review", "claims_from": "provisional"},
    {"name": "breakdown", "claims_from": "incoming"},
    {"name": "test", "claims_from": "incoming"},
])
```

The role names come from the orchestrator's config — one place, one convention. The server just stores them.

### 3. Server validates against registered roles

When a task is created with `role: "implement"`, the server checks the roles table. Unknown roles are rejected with a clear error instead of silently failing at claim time.

When a claim comes in with `role_filter: "implement"`, the server can look up `claims_from` to know which queue to search — instead of the orchestrator having to specify the queue.

### 4. No TypeScript enums

Remove `TaskRole` from the server's type system entirely. The `role` column is `TEXT` — the server treats it as an opaque string that must exist in the `roles` table. No hardcoded values to keep in sync.

### 5. Python has one Role type

```python
# Loaded from agents.yaml at startup, not hardcoded
REGISTERED_ROLES = set()  # populated during orchestrator registration
```

Or keep a `Literal` type for IDE support but generate it from the same config that gets registered.

## What This Unlocks

- **Custom roles per project.** A project could define `"design_review"` or `"security_audit"` without touching server code.
- **No cross-repo sync.** Adding a role is a config change in the orchestrator, not a code change in two repos.
- **Validation at creation time.** `POST /tasks` with `role: "implmenter"` (typo) gets a 400 instead of a task that can never be claimed.
- **`claims_from` moves to the right place.** Currently the orchestrator has `claim_from` in agents.yaml AND in flow YAML. The server could derive it from the role definition.

## Migration

1. Add `roles` table + migration
2. Add `POST /api/v1/roles` endpoint (upsert, idempotent)
3. Register roles in scheduler startup (alongside orchestrator registration)
4. Add validation to task create: `role` must exist in `roles` table
5. Remove `TaskRole` type from server TypeScript
6. Remove `AGENT_TO_TASK_ROLE` mapping from `orchestrator/tasks.py`
7. Unify Python to use one role string convention everywhere

## What the Server Still Owns

- **Queue names** (`incoming`, `claimed`, `provisional`, `done`, `failed`) — these are part of the state machine, which IS server logic
- **Transition rules** — which queue transitions are valid
- **Decision values** (`approve`, `reject`) — part of the flow/step contract

Roles are different — they're about what kind of work exists, which is deployment-specific.

## Context

Discovered while debugging why agents couldn't claim tasks after a server redeploy. The scheduler sends `role_filter='implementer'` (agent config), server filters `WHERE role IN ('implementer')`, but tasks have `role='implement'`. 69 consecutive failures, invisible until investigation.

## Open Questions

- Should `claims_from` be part of the role definition (server knows it) or stay in the flow definition (orchestrator knows it)?
- Should the roles table track which orchestrator registered each role, for multi-orchestrator setups?
- Should unknown roles on task creation be a warning (accept but flag) or hard error (400)?

## Possible Next Steps

- Add roles table + registration endpoint to server
- Register roles in scheduler startup
- Add role validation to task creation
- Remove hardcoded role types from both codebases
