# Lightweight DevOps: Proactive Queue Diagnostics

**Draft:** #53  
**Author:** human  
**Status:** idea  
**Date:** 2026-02-20

## The Problem

We just lost hours to three tasks stuck in the "claimed" queue, each failing silently in a different way:

1. **Infinite requeue loop:** The `run_tests` flow step kept timing out at 300s. The error handler logged the timeout, requeued to `incoming`, the task got reclaimed, and the same step timed out again. Endlessly.

2. **Transient error cascade:** `create_pr` failed because `gh pr view` hit a rate limit or network error. The code fell through to `gh pr create`, which failed because a PR already existed. The task got stuck in a half-broken state.

3. **Recovery that doesn't recover:** `handle_agent_result` logs errors and defers to the lease monitor for recovery. The lease monitor requeues to `incoming`. The task restarts the same failing cycle from the top.

The scheduler logs all of this, but nobody sees it unless they manually dig through logs. The `/queue-status` skill reports tasks as "ORPHANED" or "no-pid" but gives no indication of *why* — no error history, no cycle detection, no actionable diagnosis.

**The core issue is not that errors happen — it's that we have no feedback loop between errors and human attention.** The system quietly retries forever, and the only signal is "huh, that task has been running a long time."

## Design Principles

We don't want monolithic observability infrastructure. No Prometheus, no Grafana, no PagerDuty, no ELK stack. The system is a Python scheduler running via launchd every 5 seconds against a Cloudflare Workers D1 backend. The tooling should match the scale:

- **Local-first.** Diagnostics run inside the scheduler and dashboard, not in a separate monitoring service.
- **File and API native.** Alerts are files, API annotations, or webhook POSTs — not a metrics pipeline.
- **Additive.** Each pattern below is independently useful. No "you need all five for any of them to work."
- **Zero new infrastructure.** Everything runs in the existing scheduler loop or as a dashboard enhancement.

## Proposed Patterns

### 1. Task Health Annotations

Store error history on the task record itself, not just in scheduler logs.

**Fields to add (metadata or dedicated columns):**

```
last_error: str          # Most recent error message
error_count: int         # Total errors since last successful state transition
last_error_at: datetime  # When the last error occurred
requeue_count: int       # Times moved back to incoming from claimed/provisional
last_flow_step: str      # Which flow step was executing when the error happened
```

**Why:** This is the foundation for everything else. If the task carries its own error history, then `/queue-status`, the dashboard, and any alert mechanism can read it without parsing logs. It also makes post-mortems trivial: look at the task, see what happened.

**Implementation:** Update `handle_agent_result` and the lease monitor to write these fields via `sdk.tasks.update()` whenever they handle an error or requeue. The server schema gets a `task_health` metadata blob or dedicated columns.

### 2. Enhanced `/queue-status` Diagnostics

Transform `/queue-status` from a simple state counter into an active diagnostic tool.

**Checks to run:**

| Check | Signal | Severity |
|-------|--------|----------|
| Task in `claimed` > 10 minutes with no PID | Zombie claim | Warning |
| Task in `claimed` > 30 minutes | Stuck task | Error |
| Task `requeue_count` > 2 | Retry loop | Error |
| Same `last_flow_step` failing across multiple tasks | Systemic step failure | Error |
| Task in `provisional` > 1 hour | PR review stalled | Info |
| `error_count` > 0 on any active task | Recent failure | Warning |

**Output format:**

```
Queue Status
============
incoming: 2    claimed: 1    provisional: 3    done: 47    failed: 2

DIAGNOSTICS
  ERROR  TASK-fix-auth (claimed 2h 14m) — retry loop detected
         last_error: "run_tests timed out after 300s"
         requeue_count: 5, last_flow_step: run_tests
  
  ERROR  TASK-add-logging (claimed 45m) — stuck in create_pr
         last_error: "gh pr create: already exists"
         requeue_count: 3, last_flow_step: create_pr
  
  WARN   TASK-refactor-db (claimed 12m, no PID)
         Claimed but no agent process found. Lease monitor will reclaim in 3m.
```

This turns queue-status from "I see numbers" into "I see the problem and can act on it."

### 3. Circuit Breaker for Flow Steps

If a flow step keeps failing for a task, stop retrying and move the task to `failed` with a clear error.

**Rules:**

- **Max retries per step:** 3 (configurable per step in flow definition)
- **On breach:** Move task to `failed` queue with `failure_reason` containing the step name, error history, and a note that the circuit breaker tripped
- **Backoff option:** Instead of immediate requeue, delay requeue by `2^attempt * 60s` (2 min, 4 min, 8 min). Can be done by setting a `requeue_after` timestamp on the task.

**Implementation sketch in handle_agent_result:**

```python
def handle_agent_result(task, result):
    if result.is_error:
        task.error_count += 1
        task.last_error = result.error
        task.last_error_at = now()
        task.last_flow_step = result.step
        
        if task.error_count >= MAX_RETRIES_PER_STEP:
            sdk.tasks.update(task.id, queue="failed", 
                failure_reason=f"Circuit breaker: {result.step} failed {task.error_count} times. "
                               f"Last error: {result.error}")
            log.error(f"Circuit breaker tripped for {task.id} at step {result.step}")
            return
        
        # Requeue with backoff
        task.requeue_after = now() + timedelta(seconds=2**task.error_count * 60)
        sdk.tasks.update(task.id, queue="incoming", **task.health_fields())
```

This directly fixes the infinite requeue loop problem. Tasks that can't make progress get stopped, surfaced, and left for a human to investigate.

### 4. Structured Health Log

Complement the raw scheduler log with a structured health log that captures only anomalies.

**File:** `.octopoid/runtime/health.jsonl`

**Events:**

```jsonl
{"ts":"2026-02-20T10:15:00Z","level":"error","event":"circuit_breaker","task":"TASK-fix-auth","step":"run_tests","error_count":3,"message":"Circuit breaker tripped"}
{"ts":"2026-02-20T10:15:00Z","level":"warn","event":"requeue_loop","task":"TASK-add-logging","requeue_count":3,"step":"create_pr","message":"Task requeued 3 times in 2 hours"}
{"ts":"2026-02-20T10:20:00Z","level":"warn","event":"step_pattern","step":"run_tests","failures_today":5,"message":"run_tests has failed 5 times across 3 tasks today"}
```

**Why JSONL, not a database:** It's greppable, appendable, trivially parseable, and can be tailed in a terminal. Rotate daily or on size. No schema migrations, no server dependency.

**Scheduler health summary:** At the end of each scheduler tick, if any health events were generated, log a one-line summary: `"HEALTH: 2 errors, 1 warning — see .octopoid/runtime/health.jsonl"`

### 5. Alert Surface: Dashboard Health Banner + Optional Webhook

The health log is only useful if someone reads it. Two low-effort alert surfaces:

**A. Dashboard health banner**

The Textual dashboard already polls task state. Add a health panel at the top that:
- Reads the last N lines of `health.jsonl`
- Shows a red/yellow banner if there are recent errors/warnings
- Clicking a task ID in the banner jumps to the task detail view

This is the primary alert surface for interactive use. If the dashboard is open, problems are visible immediately.

**B. Optional webhook**

For unattended operation, a simple webhook POST when the circuit breaker trips or a pattern is detected:

```python
if config.get("alerts.webhook_url"):
    requests.post(config["alerts.webhook_url"], json={
        "text": f"Octopoid: circuit breaker tripped for {task.id} at {step}",
        "level": "error"
    })
```

Works with Slack incoming webhooks, Discord webhooks, ntfy.sh, or any HTTP endpoint. One config field, zero infrastructure.

**C. Terminal notification (macOS)**

For local dev, `osascript` can fire a macOS notification:

```python
os.system(f'osascript -e \'display notification "{message}" with title "Octopoid"\'')
```

Lightweight, no dependencies, only useful for single-machine setups but that's what we have.

### 6. Better Error Logging in Flow Steps

The current pattern — catch exception, log it, move on — loses critical context. Each flow step error should log:

1. **What failed:** The step name and the specific command/API call
2. **The actual error:** Full exception message, not just "step failed"
3. **What the system did about it:** "Requeued to incoming", "Circuit breaker tripped", "Moved to failed"
4. **What a human should do:** "Check if PR #45 exists and close duplicates", "Verify network connectivity"

**Example improvement for create_pr:**

```python
# Before (current)
except Exception as e:
    log.error(f"create_pr failed: {e}")
    # ... requeue

# After
except Exception as e:
    error_context = {
        "step": "create_pr",
        "task": task.id,
        "error": str(e),
        "recovery": "requeued to incoming",
        "hint": "If error is 'already exists', check for orphaned PR with: "
                f"gh pr list --head {task.branch} --state all"
    }
    log.error(f"Flow step failed: {json.dumps(error_context)}")
    health_log.write(error_context)
```

### 7. Scheduler Self-Diagnostics

At the end of each tick (or every Nth tick), the scheduler runs a brief self-check:

- **Cycle detection:** Any task that has been in `claimed` queue AND `incoming` queue in the last hour more than twice is cycling.
- **Step failure aggregation:** If the same flow step has failed across multiple tasks, it's likely a systemic issue (e.g., GitHub API down, test infra broken).
- **Stale claim detection:** Tasks in `claimed` with no PID and `claimed_at` > lease timeout are zombies.
- **Queue depth warning:** If `incoming` queue has been > 10 tasks for > 30 minutes, agents may not be spawning.

These checks write to the health log and, optionally, fire alerts.

## Implementation Priority

| Phase | What | Effort | Impact |
|-------|------|--------|--------|
| **1** | Task health annotations (fields on task) | Small | Foundation for everything else |
| **1** | Circuit breaker in handle_agent_result | Small | Directly fixes infinite retry loops |
| **2** | Enhanced `/queue-status` diagnostics | Medium | Makes problems immediately visible |
| **2** | Better error logging in flow steps | Medium | Makes post-mortems possible |
| **3** | Structured health log (health.jsonl) | Small | Persistent anomaly record |
| **3** | Dashboard health banner | Medium | Passive alerting for interactive use |
| **4** | Webhook/notification alerts | Small | Unattended alerting |
| **4** | Scheduler self-diagnostics | Medium | Systemic issue detection |

Phase 1 is two small changes that would have prevented the exact incident we just had. Phases 2-4 build on that foundation progressively.

## Open Questions

- **Alert delivery:** Terminal notification vs. dashboard banner vs. webhook to Slack — which is the primary channel? Probably dashboard banner for day-to-day, webhook for overnight/unattended runs. Worth supporting both?
- **Health annotation storage:** Dedicated columns on the tasks table, or a metadata JSON blob? Columns are queryable but require migration. Metadata is flexible but harder to filter on.
- **Backoff vs. circuit breaker:** Should we try exponential backoff before the circuit breaker trips, or just count attempts and hard-stop? Backoff buys time for transient issues (rate limits) to resolve. Circuit breaker prevents waste. Probably both: backoff for first N retries, then circuit break.
- **Health log retention:** How long to keep health.jsonl? Daily rotation? Size-based? Or just truncate on scheduler restart? Probably daily rotation with 7-day retention — simple and sufficient.
- **Idempotency in flow steps:** The `create_pr` bug (tries to create when one exists) is a design flaw, not just a logging gap. Should flow steps be idempotent by contract? i.e., `create_pr` should check-then-create atomically, `run_tests` should handle existing test runs, etc. This is a separate but related concern.
- **Error count scope:** Should `error_count` be per-step or per-task? Per-step is more precise (a task could fail at step A twice, then fail at step B once — should that be 3 or 1?). Per-task is simpler and catches "this task is cursed" scenarios. Maybe both: `error_count` (total) and `step_error_count` (current step).
