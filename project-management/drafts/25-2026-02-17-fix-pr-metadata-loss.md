# Fix PR Metadata Loss: Include PR Info in Submit Payload

**Status:** Idea
**Captured:** 2026-02-17

## Raw

> The `_submit_to_server` function does the transition and PR update as two separate calls. The submit should include the PR info in the same request. Or the submit endpoint should accept `pr_url` and `pr_number` as part of the submit payload.

## Problem

Tasks frequently end up in `provisional` with no `pr_number` or `pr_url`. This has happened with TASK-1597e6f5, TASK-server-flow-migration, and TASK-3288d983. It means `approve_and_merge()` can't find the PR to merge.

**Root cause:** The `submit-pr` script does two separate API calls:
1. `POST /api/v1/tasks/:id/submit` — transitions to provisional
2. `PATCH /api/v1/tasks/:id` — sets `pr_url` and `pr_number`

If the PATCH fails (timeout, server error, race with scheduler), the PR info is lost. The task is in provisional but nobody knows which PR it is.

## Fix

Include `pr_url` and `pr_number` in the submit payload so it's atomic:

**Server side** (`submodules/server/src/routes/tasks.ts`):
```typescript
// POST /api/v1/tasks/:id/submit
// Accept pr_url and pr_number in the body
const { commits_count, turns_used, pr_url, pr_number } = await c.req.json()
// Include in the UPDATE statement alongside submitted_at
```

**Client side** (`.octopoid/agents/implementer/scripts/submit-pr`):
```python
payload = json.dumps({
    "commits_count": commits,
    "turns_used": 0,
    "pr_url": pr_url,
    "pr_number": pr_number,
}).encode()
```

Then delete the separate PATCH call for PR info.

## Context

The `submit-pr` script is called by agents after creating a PR. It transitions the task from claimed to provisional. The scheduler also processes `result.json` when the agent exits, but if the script already submitted, the scheduler skips the transition — and may also skip the PR metadata update from `result.json`.

## Open Questions

- Should we also fix the scheduler's `handle_agent_result` to always update PR metadata from `result.json`, even if the task is already in provisional?

## Possible Next Steps

- Modify server submit endpoint to accept PR fields
- Update submit-pr script to send PR info in submit call
- Remove the separate PATCH call
- Add a safety net in the scheduler to backfill PR info from result.json
