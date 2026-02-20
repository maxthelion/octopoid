# Fix claim endpoint `queue` parameter typing and handling

**Priority:** P0
**Context:** Gatekeeper agent claiming tasks from wrong queue

## Problem

The `ClaimTaskRequest` type doesn't include a `queue` field. The claim endpoint accesses it via `(body as any).queue`, which is type-unsafe. This likely caused the queue parameter to be dropped or ignored during the atomic claims refactor, resulting in the gatekeeper (which sends `queue='provisional'`) claiming tasks from `incoming` instead.

Evidence: gatekeeper sent `queue='provisional'` but claimed TASK-27adf598, which was in `incoming` and ended up in `claimed` (incoming->claimed transition, not the `claim_for_review` provisional->provisional transition).

## Fix

### 1. Add `queue` to `ClaimTaskRequest` type

In `src/types/shared.ts` (or wherever `ClaimTaskRequest` is defined):

```typescript
interface ClaimTaskRequest {
  orchestrator_id: string
  agent_name: string
  role_filter?: string | string[]
  type_filter?: string | string[]
  lease_duration_seconds?: number
  max_claimed?: number
  queue?: string  // 'incoming' | 'provisional' — which queue to claim from
}
```

### 2. Verify atomic claim uses `claimQueue` correctly

In the atomic claims refactor, the single UPDATE statement must use `claimQueue` (from `body.queue || 'incoming'`) in the WHERE clause, not a hardcoded `'incoming'`:

```sql
UPDATE tasks
SET queue = CASE WHEN ? = 'provisional' THEN 'provisional' ELSE 'claimed' END,
    ...
WHERE id = ? AND queue = ? AND version = ?
              -- ^^^^^^^^^ must be claimQueue, not hardcoded 'incoming'
```

Also, `claim_for_review` keeps the task in `provisional` (just sets `claimed_by`). The regular `claim` moves to `claimed`. The atomic UPDATE needs to handle both transitions based on `claimQueue`.

### 3. Remove `(body as any).queue` cast

Replace with proper typed access now that the type includes `queue`.

## Testing

- Claim from `incoming` with no `queue` param — should claim from incoming (default)
- Claim with `queue='provisional'` — should only find provisional tasks
- Claim with `queue='provisional'` — task should stay in `provisional` (not move to `claimed`)
- Claim with `queue='incoming'` — task should move to `claimed`

## Acceptance Criteria

- [ ] `ClaimTaskRequest` type includes `queue` field
- [ ] No `(body as any)` casts in claim endpoint
- [ ] Claim with `queue='provisional'` only returns provisional tasks
- [ ] Claim with `queue='provisional'` uses `claim_for_review` transition (task stays provisional)
- [ ] Default behavior (no queue param) unchanged
