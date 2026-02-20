# Declarative Scheduler Jobs — Making Housekeeping Extensible and Consistent with the Flow Model

**Status:** Idea
**Captured:** 2026-02-20

## Raw

> declarative scheduler jobs - making housekeeping extensible and consistent with the flow model

## Idea

The scheduler currently has hardcoded housekeeping jobs (lease monitoring, health checks, branch cleanup, etc.) baked into `run_scheduler()` as if-statement blocks. Adding a new job requires touching 4 places in a 2300-line file: the intervals dict, the function definition, the if-block in `run_scheduler()`, and the state tracking. This is inconsistent with the rest of the system, where task transitions are declarative (flows YAML) and agents are pure functions.

The idea: define scheduler jobs in YAML (like flows), make them discoverable and extensible, and potentially support agentic (LLM-based) jobs alongside scripted ones.

## Context

This came up while discussing where a new worktree/branch sweeper job would live. The current architecture forces every new periodic job into the same monolithic scheduler function. Meanwhile, the flow system proves that declarative definitions + registered step functions is a better pattern.

Related: Draft #39 covers per-job intervals and activity-aware scaling, but doesn't address the extensibility or declarativeness question.

## Current State

How jobs work today (`orchestrator/scheduler.py`):

```python
# 1. Intervals dict (line ~651)
HOUSEKEEPING_JOB_INTERVALS = {
    "lease_monitor": 120,
    "health_check": 300,
    ...
}

# 2. Function definition (scattered through file)
def _check_expired_leases(sdk): ...

# 3. If-block in run_scheduler() (line ~2326)
if is_job_due("lease_monitor"):
    try:
        _check_expired_leases(sdk)
    except Exception as e:
        logger.error(...)
    record_job_run("lease_monitor")

# 4. State tracking in scheduler_state.json
```

Problems:
- **Not extensible** — every new job requires code changes to scheduler.py
- **Not discoverable** — you have to read the code to know what jobs exist
- **No agent support** — can't define a periodic LLM-based job (e.g. "review stale PRs every hour")
- **Inconsistent** — flows are declarative YAML; jobs are imperative Python
- **No conditions/guards** — jobs always run when due, no way to gate on system state

## What Declarative Jobs Could Look Like

```yaml
# .octopoid/jobs.yaml
jobs:
  lease_monitor:
    interval: 120
    type: script
    run: orchestrator.housekeeping:check_expired_leases

  worktree_sweeper:
    interval: 3600
    type: script
    run: orchestrator.housekeeping:sweep_stale_worktrees
    conditions:
      - no_agents_running  # don't sweep while agents are active

  branch_cleanup:
    interval: 3600
    type: script
    run: orchestrator.housekeeping:cleanup_merged_branches

  stale_pr_reviewer:
    interval: 7200
    type: agent
    agent: reviewer
    prompt: "Review PRs older than 24 hours that haven't been merged"
    conditions:
      - has_stale_prs
```

Job types:
- **`script`** — calls a registered Python function (like flow steps)
- **`agent`** — spawns a Claude agent with a prompt (like flow conditions with `type: agent`)

## Open Questions

- Should jobs use the same `@register_step()` pattern as flow steps, or a separate `@register_job()` decorator?
- How do agent-type jobs interact with the pool? Do they count against agent capacity?
- Should jobs have their own state machine (pending → running → done), or is "due / not due" sufficient?
- How does this interact with draft #39's activity-aware scaling? (e.g. job intervals that adapt to system load)
- Should jobs be able to enqueue tasks? (e.g. sweeper finds a problem, creates a task to fix it)
- Where do job functions live? A dedicated `orchestrator/jobs/` directory, or alongside the code they relate to?

## Possible Next Steps

- Design the `jobs.yaml` schema (draw from both `flows/default.yaml` and `HOUSEKEEPING_JOB_INTERVALS`)
- Implement a job registry (`@register_job("name")`) that parallels `@register_step("name")`
- Refactor existing housekeeping into registered job functions
- Replace the if-block chain in `run_scheduler()` with a generic job runner loop
- Add agent-type job support (reusing the agent spawn infrastructure from flows)
