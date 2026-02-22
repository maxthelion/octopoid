# Update actions table for agent-instruction model (Draft 68)

**Priority:** P1
**Source:** Draft 68 (project-management/drafts/68-2026-02-21-actions-as-agent-instructions.md)

## Context

The actions table currently has `action_type`, `label`, `description`, and `metadata` fields. Draft 68 replaces the Python handler registry with agent-generated instructions. Actions need an `action_data` JSON field that holds button definitions with labels and commands (natural language instructions for worker agents).

The `action_type` field is no longer needed — there's no handler registry to dispatch to. Actions are now generic proposals with structured button data.

## Current schema

```sql
-- columns: id, entity_type, entity_id, action_type, label, description,
--          status, proposed_by, proposed_at, executed_at, result,
--          expires_at, metadata, scope
```

## Required changes

### 1. Add `action_data` column (TEXT, nullable)

Stores a JSON object with button definitions:

```json
{
  "buttons": [
    {"label": "Archive", "command": "Set draft 50 status to superseded via the SDK."},
    {"label": "Enqueue remaining work", "command": "Create a task to implement the inbox processor from draft 68. Priority P1, role implement."}
  ]
}
```

### 2. Make `action_type` optional (nullable)

We're phasing out `action_type` since there's no handler registry to dispatch to. Keep the column for now but stop requiring it on create.

### 3. Update create endpoint

- Accept `action_data` (optional TEXT/JSON field)
- Make `action_type` optional (default to `"proposal"` if not provided)

### 4. Return `action_data` in responses

Include `action_data` in all GET responses (list, get by ID).

## Acceptance Criteria

- [ ] Migration adds `action_data TEXT` column to actions table
- [ ] `action_type` is no longer required on POST (defaults to `"proposal"`)
- [ ] POST /api/v1/actions accepts and stores `action_data`
- [ ] GET responses include `action_data`
- [ ] Existing actions without `action_data` still work (column is nullable)
