# Atomic claim must respect queue parameter and transition type

**Priority:** P0
**Context:** Follow-up to atomic claims refactor (TASK-atomic-claims)

## Problem

The atomic claims refactor merged the two-step claim (executeTransition + separate UPDATE) into a single UPDATE. But the single UPDATE may have lost the distinction between:

1. **Regular claim** (`incoming` -> `claimed`): task moves to `claimed` queue
2. **Review claim** (`provisional` -> `provisional`): task stays in `provisional`, just sets `claimed_by`

The old code chose the transition based on `claimQueue`:
```typescript
const transition = claimQueue === 'provisional'
  ? TRANSITIONS.claim_for_review  // from: provisional, to: provisional
  : TRANSITIONS.claim             // from: incoming, to: claimed
```

The atomic UPDATE must replicate this: when claiming from `provisional`, the target queue is `provisional` (not `claimed`).

## Fix

The atomic UPDATE should be:

```typescript
const targetQueue = claimQueue === 'provisional' ? 'provisional' : 'claimed'

const result = await execute(db,
  `UPDATE tasks
   SET queue = ?,
       version = version + 1,
       claimed_by = ?,
       claimed_at = datetime('now'),
       lease_expires_at = ?,
       orchestrator_id = ?,
       updated_at = datetime('now')
   WHERE id = ? AND queue = ? AND version = ?`,
  targetQueue,        // 'claimed' or 'provisional' depending on source
  body.agent_name,
  leaseExpiry,
  body.orchestrator_id,
  task.id,
  claimQueue,         // WHERE queue matches source
  task.version
)
```

Key: `targetQueue` is `'provisional'` for review claims and `'claimed'` for regular claims.

## Also verify

- Guards (role_matches, dependency_resolved) are still checked before the UPDATE
- History is recorded after the UPDATE with the correct event ('review_claimed' vs 'claimed')
- The `claims_from` lookup from the roles table integrates correctly

## Acceptance Criteria

- [ ] Claim from `incoming` → task moves to `claimed`
- [ ] Claim from `provisional` → task stays in `provisional` (with claimed_by set)
- [ ] WHERE clause uses `claimQueue` (not hardcoded)
- [ ] History event is 'review_claimed' for provisional claims
