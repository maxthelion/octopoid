# Server: Implement transition table for task state changes

## Problem

Each transition endpoint (claim, submit, accept, reject, requeue, force-queue) manually sets/clears its own list of columns. There's no shared concept of "what does a task in state X look like." Fields that should be cleared on a transition survive by accident — `claimed_by` on submit (fixed in 61c0918), `needs_intervention` on accept (still broken), etc.

## Fix

Add a shared `applyTransition(task, fromQueue, toQueue, payload)` function that:
1. Looks up the transition in a transition table
2. Applies the declared field resets
3. Merges the endpoint-specific payload on top
4. Bumps version and sets updated_at

### Transition table

```typescript
const TRANSITIONS: Record<string, Record<string, FieldResets>> = {
  "claimed -> provisional": {
    claimed_by: null,
    claimed_at: null,
    lease_expires_at: null,
    orchestrator_id: null,
    needs_intervention: false,
  },
  "provisional -> done": {
    claimed_by: null,
    claimed_at: null,
    lease_expires_at: null,
    orchestrator_id: null,
    needs_intervention: false,
    // completed_at set by endpoint
  },
  "provisional -> incoming": {
    claimed_by: null,
    claimed_at: null,
    lease_expires_at: null,
    orchestrator_id: null,
    // rejection_count incremented by endpoint
  },
  "incoming -> claimed": {
    // claimed_by, claimed_at, lease_expires_at, orchestrator_id set by endpoint
  },
  "* -> failed": {
    claimed_by: null,
    claimed_at: null,
    lease_expires_at: null,
    orchestrator_id: null,
    needs_intervention: false,
  },
  "* -> incoming": {
    claimed_by: null,
    claimed_at: null,
    lease_expires_at: null,
    orchestrator_id: null,
    needs_intervention: false,
  },
};
```

### Migration path

1. Add `applyTransition()` as a shared function
2. Migrate each endpoint to call it instead of hand-rolling UPDATE queries
3. Add tests that verify field resets for each transition
4. force-queue continues to bypass the table (admin escape hatch) but logs a warning if the result state wouldn't match

## Acceptance Criteria

- [ ] Shared `applyTransition()` function with transition table
- [ ] All 5 transition endpoints (claim, submit, accept, reject, requeue) use it
- [ ] force-queue optionally uses it (with override capability)
- [ ] Integration test: submit clears claimed_by, lease_expires_at, needs_intervention
- [ ] Integration test: accept clears claimed_by, lease_expires_at, needs_intervention
- [ ] Integration test: reject clears all claim metadata
- [ ] Integration test: invalid transitions are rejected (e.g. claimed -> done returns 400)
- [ ] Existing tests pass

## Context

- Draft 221: Server-side state machine for task transitions
- Draft 216: Task state should be a state machine with enforced transitions
- Server task: clear-claimed-by-on-submit.md (subsumed by this work)
