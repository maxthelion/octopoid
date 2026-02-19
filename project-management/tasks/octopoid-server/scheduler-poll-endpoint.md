# Add scheduler poll endpoint

## Problem

The scheduler makes ~14 separate API calls per tick just to read queue state (backpressure counts, provisional task list, registration check). At 60s intervals that's ~23,000 requests/day against a 10,000/day Cloudflare Workers limit.

## Solution

Add `GET /api/v1/scheduler/poll` that returns everything the scheduler needs in one call.

### Response shape

```json
{
  "queue_counts": {
    "incoming": 4,
    "claimed": 2,
    "provisional": 1
  },
  "provisional_tasks": [
    {
      "id": "TASK-abc",
      "hooks": "[{...}]",
      "pr_number": 87,
      "claimed_by": null
    }
  ],
  "orchestrator_registered": true
}
```

### Fields

- **`queue_counts`** — count of tasks per queue (incoming, claimed, provisional). Used by `backpressure.py` to decide whether to claim. Replaces 3+ `sdk.tasks.list()` calls per agent.
- **`provisional_tasks`** — list of provisional tasks with their hooks and PR info. Used by `process_orchestrator_hooks()`. Replaces `sdk.tasks.list(queue="provisional")` + per-task gets.
- **`orchestrator_registered`** — whether this orchestrator ID is already registered. Caller passes `?orchestrator_id=<id>` as query param. Replaces the POST to `/api/v1/orchestrators/register` on most ticks.

### Implementation

This is a server-side change in `packages/server/`.

1. Add route `GET /api/v1/scheduler/poll?orchestrator_id=<id>`
2. Run three COUNT queries in parallel:
   ```sql
   SELECT queue, COUNT(*) as count FROM tasks WHERE queue IN ('incoming', 'claimed', 'provisional') GROUP BY queue
   ```
3. Fetch provisional tasks (only id, hooks, pr_number, claimed_by — lightweight)
4. Check orchestrator registration with a simple EXISTS query
5. Return combined JSON

### Acceptance criteria

- [ ] `GET /api/v1/scheduler/poll?orchestrator_id=prod-xxx` returns the shape above
- [ ] Queue counts are accurate (match individual list endpoints)
- [ ] Provisional tasks include hooks and pr_number fields
- [ ] orchestrator_registered is true/false based on whether the ID exists
- [ ] Endpoint responds in <50ms (it's just COUNT queries + a small SELECT)
- [ ] Add integration test in `tests/integration/`

### Notes

- This is the server half. The scheduler refactor to consume this endpoint is a separate task.
- See `project-management/drafts/39-2026-02-18-independent-tick-intervals.md` for full context.
