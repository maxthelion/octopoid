# Enforce scope isolation on flows and add DELETE endpoint

**Priority:** P1

## Context

Flows have no scope isolation. All flows are stored with `scope=NULL` and bleed across projects. When one project registers a flow called "fast" or "qa", every other project sees it. This is inconsistent with how tasks, drafts, and messages are scoped.

Additionally, there's no DELETE endpoint for flows — stale test flows (`test-flow-upsert`) can't be cleaned up.

Currently registered flows all show `scope=None`:
```
default  scope=None
fast     scope=None
qa       scope=None
test-flow-upsert  scope=None
```

## Changes needed

### 1. Add scope to flows table

```sql
ALTER TABLE flows ADD COLUMN scope TEXT;
CREATE INDEX idx_flows_scope ON flows(scope);
```

### 2. Enforce scope on all flow endpoints

- `PUT /api/v1/flows/:name` (register/upsert) — require scope from auth context, store it
- `GET /api/v1/flows` (list) — filter by scope from auth context
- Each scope should have its own namespace: two projects can both have a flow called "default" without conflict

The scope + name should be the unique key (not just name alone).

### 3. Add DELETE endpoint

```
DELETE /api/v1/flows/:name
```

Deletes the flow matching the given name within the current scope. Returns 404 if not found.

### 4. Clean up existing data

Migration should set `scope = 'octopoid'` on existing flows (or whatever the default scope is for this installation). Delete `test-flow-upsert` if it still exists.

## Acceptance Criteria

- [ ] Flows table has scope column
- [ ] PUT /api/v1/flows/:name stores scope from auth context
- [ ] GET /api/v1/flows filters by scope
- [ ] Scope + name is the unique constraint (not name alone)
- [ ] DELETE /api/v1/flows/:name endpoint works
- [ ] Existing flows migrated to have a scope
- [ ] test-flow-upsert cleaned up in migration
