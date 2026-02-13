# Task: GitHub issue monitor should handle closed issues

**Priority:** P2

## Problem

The github-issue-monitor creates tasks for GitHub issues but doesn't handle issue closure. When a GitHub issue is closed, the corresponding task remains in whatever queue it's in (incoming, provisional, etc.). This leads to stale tasks showing up in the dashboard.

**Example:** GH-12 was closed on GitHub but `gh-12-636af9b8` was still sitting in the `provisional` queue, showing as "IN REVIEW" in the dashboard.

## Expected Behavior

When the github-issue-monitor runs, it should:
1. Check if any existing tasks correspond to GitHub issues that are now closed
2. Move those tasks to `done` (or a new `cancelled` queue) automatically
3. Optionally add a note: "GitHub issue was closed"

## Files to Investigate

- `orchestrator/roles/github_issue_monitor.py` — the monitor role
- `orchestrator/scheduler.py` — where the monitor is invoked

## Acceptance Criteria

- Tasks for closed GitHub issues are automatically moved out of active queues
- Dashboard no longer shows stale tasks for closed issues
- Monitor logs when it detects and handles a closed issue
