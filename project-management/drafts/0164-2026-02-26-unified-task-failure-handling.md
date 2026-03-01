# Unified task failure handling and logging

**Captured:** 2026-02-26

## Raw

> Can we do an audit of all the places where a task can be moved to failed? eg via sdk.tasks.update(task_id, queue='failed'). Perhaps we have an explicit sdk.tasks.fail(task_id, reason) method that logs the failure. It should be super easy to answer the question "why did it fail?"

## Idea

All paths to the `failed` queue should go through a single function that logs the reason, writes to the task log, and sets `execution_notes`. Currently 5 different callsites use raw `sdk.tasks.update(queue='failed')` with inconsistent logging. It should be trivially easy to answer "why did this task fail?" for any failed task.

## Context

Discovered while investigating task 2a06729d (pip package rename). The PR merged successfully but `update_changelog` failed afterward, and the catch-all exception handler dumped the task to `failed`. The `execution_notes` field was `None` — no breadcrumb explaining what happened. The task log (`.octopoid/logs/tasks/TASK-*.log`) had no FAILED entry either. It took a 15-minute forensic investigation to figure out what went wrong.

## Audit: Current paths to `failed`

| # | File | Line | Trigger | Sets `execution_notes`? | Writes task log? |
|---|------|------|---------|------------------------|-----------------|
| 1 | `result_handler.py` | 496 | Flow dispatch crash (unhandled exception in `handle_agent_result_via_flow`) | Yes | No |
| 2 | `result_handler.py` | 573 | 3 consecutive step failures in `handle_agent_result` | Yes | No |
| 3 | `scheduler.py` | 1713 | Circuit breaker: lease expired N times | Yes | No |
| 4 | `scheduler.py` | 2014 | Circuit breaker: spawn failed N times | Yes | No |
| 5 | `scheduler.py` | 275 | Guard: empty task description blocks spawn | No | No |

**Additional issues:**
- `fail_task()` in `tasks.py:306` exists but is **never called** — dead code
- No dedicated server-side `fail` endpoint — unlike `reject` and `accept` which record `task_history` entries, failing is a raw PATCH that skips the audit trail
- No way to recover a task incorrectly moved to `failed` (server blocks `failed → done` via both PATCH and accept)
- The SDK has no `sdk.tasks.fail()` method

## Proposed fix

### 1. Add `fail_task()` as the single canonical path

Create (or fix the existing dead) `fail_task(task_id, reason, source)` function in `orchestrator/tasks.py`:

```python
def fail_task(task_id: str, reason: str, source: str = "unknown") -> None:
    """Move a task to failed with full logging.

    This is the ONLY way tasks should be moved to failed.
    """
    sdk = get_sdk()

    # 1. Set execution_notes on the server (always)
    sdk.tasks.update(task_id, queue="failed", execution_notes=reason)

    # 2. Write to the task log file
    from .task_log import log_task_event
    log_task_event(task_id, "FAILED", source=source, reason=reason)

    # 3. Print to scheduler stdout (for launchd log capture)
    print(f"[{datetime.now().isoformat()}] FAILED {task_id} source={source} reason={reason[:200]}")
```

### 2. Replace all 5 callsites

Every `sdk.tasks.update(task_id, queue='failed', ...)` becomes `fail_task(task_id, reason=..., source=...)` with a descriptive source tag:

| Callsite | `source` value |
|----------|---------------|
| Flow dispatch crash | `flow-dispatch-error` |
| 3x step failure | `step-failure-circuit-breaker` |
| Lease expiry circuit breaker | `lease-expiry-circuit-breaker` |
| Spawn failure circuit breaker | `spawn-failure-circuit-breaker` |
| Empty description guard | `guard-empty-description` |

### 3. Add `sdk.tasks.fail()` to the Python SDK

Convenience method that calls `sdk.tasks.update(task_id, queue='failed', execution_notes=reason)`. This is a client-side convenience — no server changes needed initially.

### 4. (Future) Add server-side `/tasks/:id/fail` endpoint

Like `accept` and `reject`, a dedicated endpoint that:
- Records a `task_history` entry with the failure reason
- Sets `completed_at`
- Validates the transition

This is a server change and can be done later.

## Open Questions

- Should `fail_task()` also post a message to the task thread (for visibility in the dashboard)?
- Should we add a `failed_reason` column to the server schema (separate from `execution_notes` which is more general)?
- Should there be a `/recover-task` command that can move a task from `failed` back to `incoming` for cases like the 2a06729d bug?

## Possible Next Steps

- Replace all 5 callsites with `fail_task()` — small, safe refactor
- Add `sdk.tasks.fail()` convenience method to the Python SDK
- Add FAILED entries to `.octopoid/logs/tasks/TASK-*.log`
- Write a server task for the `/tasks/:id/fail` endpoint
- Add `/recover-task` skill for manual recovery of incorrectly-failed tasks


## Invariants

- `failure-reason-always-recorded`: Every path that moves a task to `failed` records the reason in `execution_notes` and appends a FAILED entry to the task log. It is always possible to answer "why did this task fail?" without forensic investigation.
