# Fix GitHub API Rate Limit Exhaustion

**Status:** Archived — Fix 1 (the critical one) landed. Fixes 2-3 are minor and can be revisited later.
**Captured:** 2026-02-20

## Root Cause

The dashboard (`packages/dashboard`) polls `get_project_report()` every 5 seconds. `get_project_report()` calls `_gather_prs(sdk)` which:

1. Runs `gh pr list --state open` — **1 GraphQL call**
2. For each open PR, runs `gh pr view <N> --json comments` via `_extract_staging_url()` — **1 call per PR**

With 30 open PRs, this is **31 calls every 5 seconds** = 372/minute = **22,320/hour**. GitHub's GraphQL rate limit is 5,000/hour. The dashboard alone consumes **4.5x the hourly limit**.

The CHANGELOG claims this was disabled (`"prs": []` in reports), and the PRs tab was removed from the dashboard. But `_gather_prs(sdk)` is still called at line 42 of `reports.py` — the fix either never landed or was reverted during a merge.

### Secondary consumers

These are minor compared to the dashboard but compound the problem:

- **`guard_pr_mergeable`** in scheduler.py: runs `gh pr view <PR> --json mergeable` for every claimed task that has a `pr_number`, on every scheduler tick where that agent blueprint is evaluated (~hourly for tasks with PRs). ~28 calls today.
- **`create_pr` flow step**: runs `gh pr view` + `gh pr create` per task. ~19 executions today, with repeated retries when the step fails and the task gets reclaimed.
- **Agent processes**: Claude Code agents call `gh` for PR creation, commenting, etc. Hard to count but these are one-shot, not polling.

## Impact

- All flow steps that call `gh` fail (`create_pr`, `merge_pr`, `guard_pr_mergeable`)
- Tasks get stuck in `claimed` because flow steps can't complete
- Scheduler enters retry loops (reclaim → fail → requeue → reclaim)
- Agent work is wasted — code is written but can't be submitted

## Fixes

### Fix 1: Kill `_gather_prs` for real (immediate)

Replace `_gather_prs(sdk)` with `[]` in `get_project_report()`. The PRs tab is already removed from the dashboard — nobody consumes this data.

```python
# reports.py line 42
"prs": [],  # Disabled — was burning 22k+ GraphQL calls/hour
```

Also remove the `_gather_prs`, `_extract_staging_url`, and `_store_staging_url` functions entirely. Dead code.

**Savings: ~22,000 calls/hour → 0**

### Fix 2: Rate-limit guard_pr_mergeable (scheduler)

`guard_pr_mergeable` calls `gh pr view` for a task that's been stuck with conflicts for days (TASK-review-card, PR #127). It detects the conflict every time, tries to reject, fails (409), and runs again next tick.

Two fixes:

a) **Cache the result**: if `guard_pr_mergeable` finds conflicts, store a `last_pr_check` timestamp on the task. Don't re-check for at least 30 minutes.

b) **Circuit breaker**: after 3 consecutive conflict detections with failed rejects, stop checking and log a warning. The task is stuck — a human needs to intervene.

**Savings: ~28 calls/day → ~5 calls/day**

### Fix 3: Backoff on flow step retries (scheduler)

When `create_pr` fails (rate limit, network error, etc.), the task stays in `claimed`. The lease eventually expires, the task goes to `incoming`, gets reclaimed, and fails again. Each cycle burns 2+ `gh` calls.

Add exponential backoff: after a flow step fails N times, don't requeue to incoming immediately. Either:
- Move to `failed` after 3 consecutive step failures (not agent failures)
- Add a `retry_after` timestamp so the scheduler skips reclaiming until the backoff expires

**Savings: prevents 10-20 wasted calls per stuck task per day**

### Fix 4: Move PR data to server (future)

Instead of calling `gh` from the scheduler/dashboard, store PR state on the server:
- `pr_number`, `pr_url`, `pr_mergeable` fields already exist on tasks
- When `create_pr` step succeeds, write the PR info to the server
- Dashboard reads PR data from the server report, not from `gh`
- `guard_pr_mergeable` reads cached `pr_mergeable` from server, only refreshes periodically

This eliminates all polling `gh` calls from the hot path. Only the `create_pr` and `merge_pr` steps need real GitHub API access.

## Priority

Fix 1 is **critical and immediate** — it should be a manual code change, not a task. The dashboard is actively burning rate limit right now.

Fixes 2-3 are P1 tasks.

Fix 4 is a design improvement for later.

## Estimated savings

| Source | Current calls/hour | After fixes |
|--------|-------------------|-------------|
| Dashboard `_gather_prs` | ~22,000 | 0 (Fix 1) |
| `guard_pr_mergeable` | ~28/day | ~5/day (Fix 2) |
| Flow step retries | ~40/day | ~10/day (Fix 3) |
| Agent processes | Variable | Unchanged |
| **Total** | **~22,100/hour** | **~3/hour** |
