# GitHub Issue Poller as a Scheduled Job

**Status:** Draft
**Captured:** 2026-02-20

## Summary

Reintroduce the GitHub issue poller as a scheduled job rather than a custom agent. The old implementation (`orchestrator/roles/github_issue_monitor.py`) uses the legacy `BaseRole` class and bypasses `create_task()`. Rewrite it as a `@register_job` function that runs on an interval, uses `create_task()` properly, and stays within GitHub API rate limits.

## Why a Job, Not an Agent

The issue poller is purely programmatic — no LLM needed. It runs `gh issue list` (1 API call), diffs against known issues, and calls `create_task()` for new ones. This is exactly what `type: script` jobs are for. Running it as an agent wastes a pool slot and Claude tokens on something that's just a Python function.

## Rate Limit Budget

| Action | Calls per run | Frequency | Calls/hour |
|--------|--------------|-----------|------------|
| `gh issue list` | 1 | Every 15 min | 4 |
| `gh issue comment` (per new issue) | 1 | Rare | ~0-2 |
| **Total** | | | **~4-6** |

GitHub's rate limit is 5,000/hour. This uses < 0.1%.

## Design

### Job definition (in `jobs.yaml`)

```yaml
poll_github_issues:
  interval: 900          # 15 minutes
  type: script
  run: poll_github_issues
  group: local           # no poll data needed
```

### Job function

Registered via `@register_job("poll_github_issues")` in `orchestrator/jobs.py` (or a dedicated `orchestrator/github_issues.py` if jobs.py gets too large).

```python
@register_job("poll_github_issues")
def poll_github_issues(ctx: JobContext) -> None:
    """Poll GitHub issues and create tasks for new ones."""

    # 1. Load state (which issues we've already processed)
    state = _load_issue_state()
    processed = set(state.get("processed_issues", []))

    # 2. Fetch open issues (1 gh API call)
    issues = _fetch_open_issues()

    # 3. For each new issue, create a task via create_task()
    for issue in issues:
        if issue["number"] in processed:
            continue

        _create_task_from_issue(issue)
        processed.add(issue["number"])

        # Comment on the issue (1 gh API call per new issue)
        _comment_on_issue(issue["number"], task_id)

    # 4. Save state
    _save_issue_state({"processed_issues": sorted(processed)})
```

### Key differences from old implementation

1. **Uses `create_task()`** from `orchestrator.tasks` — handles file placement and server registration correctly (per CLAUDE.md rules)
2. **No SDK init** — `create_task()` handles that internally
3. **No BaseRole** — it's a plain function with `@register_job`
4. **State file** stays at `.octopoid/runtime/github_issues_state.json`
5. **Cross-repo forwarding** for `server`-labelled issues — keep this feature, it's useful

### What to do with the old code

- Delete `orchestrator/roles/github_issue_monitor.py` — replaced by the job function
- Delete `.octopoid/agents/github-issue-monitor/` directory
- Remove the `github-issue-monitor` entry from `agents.yaml`

## Dependency

This depends on the declarative scheduler jobs system (draft 61, task `42c991a8`) being implemented first. The job function itself can be written now but won't run until the job runner exists.

Alternatively, as an interim step, the job function could be wired into the existing hardcoded scheduler loop (add another `is_job_due` block) and migrated to declarative later. This gets it running immediately.

## Implementation Steps

1. Write `poll_github_issues()` function using `create_task()` — can live in `orchestrator/github_issues.py`
2. Wire it into the scheduler (either via `@register_job` if draft 61 lands first, or via a hardcoded `is_job_due` block as interim)
3. Delete old `github_issue_monitor.py` role and agent config
4. Test: create a test issue, verify task appears in queue
