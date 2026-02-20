# Add human_review and human_approved queue types

> **Superseded** by extensible queue validation. The server now validates queue names at runtime against registered flows (migration 0011), so projects can define custom pipeline stages like `human_review` and `sanity_approved` in their flow YAML without hardcoding new queue types. See:
> - Server work (done): `project-management/tasks/octopoid-server/extensible-queue-validation.md`
> - Orchestrator work (TASK-26ff1030): `.octopoid/tasks/TASK-26ff1030.md`

Support multi-stage review pipelines where automated gatekeepers hand off to human review before merge.

## Motivation

Boxen (and likely other projects) want a workflow:
```
implementer -> sanity-check gatekeeper -> QA gatekeeper -> human review -> merge
```

This requires new queue states beyond the current `incoming -> claimed -> provisional -> done` pipeline. The flow system already supports arbitrary state names in YAML, but the server and orchestrator have hardcoded type unions that reject unknown queue values.

## Work Items

### 1. Add queue types to TypeScript (server)

**`submodules/server/src/types/shared.ts`** and **`packages/shared/src/task.ts`**:

Add to the `TaskQueue` union:
```typescript
| 'human_review'
| 'human_approved'
| 'sanity_approved'   // intermediate: passed sanity check, awaiting QA
```

These two files must stay in sync (comment in shared.ts says so).

### 2. Add TRANSITIONS entries (server)

**`submodules/server/src/state-machine.ts`**:

Add transitions so that the server can atomically move tasks between these queues:

```typescript
// Sanity-check gatekeeper submits to sanity_approved
'provisional:sanity_approved': {
  fromQueue: 'provisional',
  toQueue: 'sanity_approved',
  guards: [...],
},

// QA gatekeeper submits to human_review
'sanity_approved:human_review': {
  fromQueue: 'sanity_approved',
  toQueue: 'human_review',
  guards: [...],
},

// Human approves
'human_review:done': {
  fromQueue: 'human_review',
  toQueue: 'done',
  guards: [...],
},

// Rejections back to incoming (from any review stage)
'human_review:incoming': { ... },
'sanity_approved:incoming': { ... },
```

Also need claim transitions so gatekeepers can claim from their respective queues:
```typescript
'sanity_approved:claimed': { ... },   // QA gatekeeper claims from sanity_approved
'human_review:claimed': { ... },      // (if human review were agent-driven)
```

### 3. Add to Python TaskQueue literal (orchestrator)

**`orchestrator/config.py`**:

```python
TaskQueue = Literal[
    "incoming", "claimed", "provisional", "done", "failed",
    "rejected", "escalated", "recycled", "breakdown",
    "needs_continuation", "backlog", "blocked",
    "human_review", "human_approved", "sanity_approved",  # NEW
]
```

### 4. New flow steps (orchestrator)

**`orchestrator/steps.py`**:

```python
@register_step("submit_to_human_review")
def submit_to_human_review(task, result, task_dir):
    """Move task from QA-approved to human_review queue."""
    sdk = get_sdk()
    sdk.tasks.update(task["id"], queue="human_review")

@register_step("approve_human_review")
def approve_human_review(task, result, task_dir):
    """Human approved -- merge PR and move to done."""
    approve_and_merge(task["id"])
```

### 5. Dashboard support (optional, lower priority)

The Work tab kanban and Done tab would need awareness of new queue names to display tasks in these intermediate states. Could add columns or a pipeline view. Not blocking.

## Design Considerations

### Alternative: Reuse existing queues with claim_from

Instead of adding new queue names, you could have multiple gatekeepers all claim from `provisional` with different `agent` filters in the flow. But this doesn't give visibility into which review stage a task is at, and the flow YAML becomes ambiguous about ordering.

New queue names are cleaner: each stage has a distinct state, the dashboard can show pipeline progress, and the flow YAML reads naturally.

### Generalizing

Rather than hardcoding specific queue names, consider making the `TaskQueue` type extensible -- e.g., the server could accept any string as a queue value (the D1 schema already does). This would let each project define its own pipeline stages via flows without server changes. The TypeScript union could become a runtime validation against a configurable allowlist rather than a compile-time type.

This is a larger change but would prevent needing server deploys every time someone wants a new review stage.

## Priority

P2 -- needed for boxen migration but not blocking octopoid's own workflow.
