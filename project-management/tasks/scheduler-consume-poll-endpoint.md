# Refactor scheduler to use poll endpoint

## Problem

The scheduler makes ~14 separate API calls per tick for read operations (queue counts, provisional list, registration). This needs to collapse into a single call to `GET /api/v1/scheduler/poll`.

## Depends on

- `project-management/tasks/octopoid-server/scheduler-poll-endpoint.md` (server must ship first)

## Changes

### 1. Add `poll()` method to SDK

In `packages/python-sdk/octopoid_sdk/client.py`, add a method that calls the poll endpoint and returns the response dict.

### 2. Refactor backpressure.py

`count_queue()` currently calls `sdk.tasks.list()` for each queue. `can_claim_task()` calls `count_queue()` three times (incoming, claimed, provisional).

Replace with:
- `can_claim_task(queue_counts: dict)` — accepts pre-fetched counts instead of making its own API calls
- `count_queue()` can remain for non-scheduler callers but the scheduler path should never use it

### 3. Refactor guard_backpressure

In `orchestrator/scheduler.py`, `guard_backpressure()` calls `count_queue()` and `can_claim_task()` per agent. Instead, fetch poll data once at the start of the agent evaluation loop and pass `queue_counts` through `AgentContext` so all agents share it.

### 4. Refactor process_orchestrator_hooks

Currently calls `sdk.tasks.list(queue="provisional")` then `sdk.tasks.get()` per task. Instead, use the `provisional_tasks` list from the poll response. May still need individual `sdk.tasks.get()` after hook execution to re-check state — that's fine, it only happens when hooks actually run.

### 5. Refactor _register_orchestrator

Skip the POST if the poll response shows `orchestrator_registered: true`. Only register on first tick or after the server reports not-registered.

### 6. Per-job intervals

Add `scheduler_state.json` with per-job `last_run` timestamps:

| Job | Interval |
|-----|----------|
| `check_and_update_finished_agents` | 10s (local only) |
| `_register_orchestrator` | 300s |
| `process_orchestrator_hooks` | 60s |
| `_check_queue_health_throttled` | 1800s (already self-throttled) |
| Agent evaluation loop (remote poll) | 60s |

Reduce launchd interval to 10s. Each job checks its `last_run` and skips if not due.

## Acceptance criteria

- [ ] Scheduler makes exactly 1 API call per remote tick (the poll) plus writes (claim, reject, etc.)
- [ ] `check_and_update_finished_agents` runs every 10s (local PID checks, no API unless result found)
- [ ] Backpressure decisions use poll data, not individual list calls
- [ ] All existing scheduler tests pass
- [ ] Manually verify with `--once --debug` that tick completes correctly

## Context

See `project-management/drafts/39-2026-02-18-independent-tick-intervals.md` for the full analysis.
