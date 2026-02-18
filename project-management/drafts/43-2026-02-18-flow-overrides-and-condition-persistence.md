# Task-Level Flow Overrides and Condition Persistence

**Status:** Idea
**Captured:** 2026-02-18
**Related:** Draft 20 (Flows as Single Integration Path)

## Problem

Two features from the original flows design (Draft 20) are not yet implemented:

### 1. Task-level flow overrides

Tasks should be able to override specific transitions in their flow. Example: skip human approval for auto-merge tasks.

```yaml
id: TASK-abc123
flow: default
flow_overrides:
  "provisional -> done":
    conditions:
      - name: human_approval
        type: manual
        skip: true
```

Without this, every variation requires a separate flow file, which doesn't scale.

### 2. Condition result persistence

When conditions are evaluated, the results aren't stored on the task. This means:
- You can't tell which conditions already passed
- Re-evaluation after a restart repeats all conditions
- No audit trail of what was checked

Proposed: store `conditions_passed: ["tests_pass", "gatekeeper_review"]` on the task so the system can resume from where it left off.

## Open Questions

- How should overrides interact with condition ordering? Can you insert new conditions or only skip/modify existing ones?
- Should condition persistence be a server-side field on the task, or stored locally?
- Is there a simpler mechanism than full flow_overrides? (e.g. task-level flags like `auto_merge: true`)
