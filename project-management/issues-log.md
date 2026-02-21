# Issues Log

Known symptoms and their root causes. **Consult this first when diagnosing a problem** — many issues recur.

## Scheduler not processing tasks

| Symptom | Likely cause | See |
|---|---|---|
| "Last tick: Xh ago" in queue-status | Scheduler crashed or launchd throttled | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md) |
| Scheduler ticks but does nothing | Syntax error in scheduler.py (stale `__pycache__` masking it) | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md) |
| Tasks stuck in `claimed` with empty `running_pids.json` | Orphaned tasks — result collection failed, lease expiry requeue loop | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md) |
| Tasks cycling between `incoming` and `claimed` repeatedly | Lease expiry requeue fighting with failed result collection | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md) |

## Agent worktree issues

| Symptom | Likely cause | See |
|---|---|---|
| Uncommitted changes in main tree matching agent work | Agent used absolute paths outside worktree; fix: project-relative permissions in worktree `.claude/settings.json` | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md#fixes-applied-related-issues-found-during-investigation) |
| PR reverts bug fixes from main | Agent branch diverged from old main; gatekeeper should reject with "rebase first" | [PR #154 rejection comment](https://github.com/maxthelion/octopoid/pull/154) |

## Dashboard

| Symptom | Likely cause | See |
|---|---|---|
| Turn counter shows 0/100t for all tasks | PostToolUse hook not writing `tool_counter` file; check worktree `.claude/settings.json` | [2026-02-21 postmortem](postmortems/2026-02-21-scheduler-crash-orphaned-tasks.md#fixes-applied-related-issues-found-during-investigation) |
| Dashboard clears PID tracking | `cleanup_dead_pids()` called from wrong codepath; only `check_and_update_finished_agents` should remove PIDs | [test_pid_lifecycle.py](../tests/test_pid_lifecycle.py) |

## Stale state

| Symptom | Likely cause | See |
|---|---|---|
| Scheduler runs old code after editing `.py` files | Stale `__pycache__`; run `find orchestrator -name '__pycache__' -type d -exec rm -rf {} +` | [CLAUDE.md](../CLAUDE.md#scheduler-and-python-caching) |
| Agent marked as failed despite completing work | Stale `result.json` from previous run | [2026-02-15 postmortem](postmortems/2026-02-15-TASK-stale-result-60f52b91.md) |

## API / Server

| Symptom | Likely cause | See |
|---|---|---|
| Cloudflare rate limiting | Too many agents polling simultaneously; consider request batching | — |
| `_gather_prs` burning API calls | Function not disabled when expected; verify before trusting plan claims | [CLAUDE.md](../CLAUDE.md#plan-verification-rule) |
