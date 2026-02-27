# Dashboard Does Not Live-Update Drafts

**Captured:** 2026-02-24

## Raw

> Drafts don't seem to update automatically in the dashboard. Is it polling for more information, or only on load?

## Idea

The dashboard's drafts tab doesn't refresh automatically when new drafts are created or existing ones change status. You have to manually press R to see updates.

## Root Cause

The dashboard polls every 5 seconds (`app.py:94`), but uses a lightweight `/scheduler/poll` endpoint that only returns **queue counts** (incoming, claimed, provisional, done, failed). If the counts haven't changed, it skips the full data fetch (`app.py:122-124`).

Creating or updating a draft doesn't change any task queue count, so the poll says "nothing changed" and the drafts tab never refreshes.

The drafts tab only updates when:
1. **On startup** — `_fetch_data(force=True)` at mount
2. **Manual refresh** — pressing R triggers `action_refresh()` → `_fetch_data(force=True)`
3. **A task queue changes** — incidental, causes a full fetch that also pulls drafts

## Possible Fixes

1. **Add draft count to poll response** — include a `draft_count` or `draft_updated_at` field in `/scheduler/poll` so the dashboard detects draft changes
2. **Periodic forced refresh** — every Nth poll cycle, do a full fetch regardless (e.g. every 60s)
3. **Separate draft polling** — poll drafts independently on a slower interval (e.g. every 30s)
4. **Accept it** — drafts change infrequently enough that manual R is fine

## Open Questions

- Is this worth fixing, or is manual refresh acceptable for drafts?
- Does the poll endpoint live on the server (would need a server change) or is it computed locally?
- Should other non-queue data (inbox messages, agent status) also be considered stale in the same way?

## Possible Next Steps

- Check what `/scheduler/poll` returns and whether it's easy to add a draft hash/count
- If server-side, write a server task to `project-management/tasks/octopoid-server/`
- If client-side, fix in `packages/dashboard/app.py`
