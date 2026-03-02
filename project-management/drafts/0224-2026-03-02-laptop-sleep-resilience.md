# Draft 0224: Laptop Sleep Resilience — Lease Expiry, Session Death, Submit 409s

**Status:** Implemented
**Author:** Agent (d968986b)
**Date:** 2026-03-02

---

## Problem

When a laptop sleeps, the Octopoid scheduler is also suspended. Upon wake, several things can
go wrong:

1. **Lease expiry**: Agent leases (default 1h) expire during sleep, leaving tasks in `claimed`
   with no active agent. On wake, `check_and_requeue_expired_leases()` kills the agent process
   and requeues the task — discarding potentially hours of work.

2. **Agent session death**: The scheduler-invoked Claude agent (a subprocess) continues to run
   on macOS through sleep. When it wakes, it resumes mid-task. But if the lease expired, its
   next submit call gets a 409.

3. **Submit 409s**: When an agent tries to submit a task whose lease has expired, the server
   returns 409. There is no automatic retry — the task goes to `requires-intervention`.

4. **No detection or recovery**: Nothing tells the agent "you woke from sleep, renew your claim".

---

## Investigation: Approaches

### 1. Lease Renewal — Proactive Extension

**What**: A new scheduler job `renew_active_leases()` runs every 60 seconds. For each task
in `claimed` whose lease is within 30 minutes of expiry (or already expired) AND has an alive
agent process, extend the lease by 1 hour.

**Pros:**
- Fully automatic — no agent changes required
- Handles sleep, slow agents, and long tasks uniformly
- Agent never sees a 409 from lease expiry while running
- Can be tested without sleep simulation

**Cons:**
- Relies on process liveness check; a stuck/hung agent will keep getting renewed
- Doesn't bound total task duration (a hung agent can hold a task indefinitely)

**Mitigations:**
- The circuit breaker on `check_and_requeue_expired_leases` (attempt_count threshold) still
  fires if the process dies and restarts repeatedly
- Humans can force-fail tasks held too long via the dashboard

**Assessment:** Robust, low-risk, directly solves the core problem. **Recommended.**

---

### 2. Sleep Detection — Gap-Based Wake Reconciliation

**What**: Track `last_tick` timestamp in scheduler state. On each wake, if the gap exceeds
a threshold (e.g. 5 minutes), log a warning and force-run lease renewal immediately.

**Pros:**
- Provides observability (log entry when wake-from-sleep is detected)
- Can trigger immediate remediation on first tick after wake

**Cons:**
- Redundant with lease renewal (which already handles expired leases proactively)
- Gap threshold tuning needed (false positives on slow CI machines)
- Does not add functionality beyond logging when combined with lease renewal

**Assessment:** Nice-to-have for observability. Implemented as lightweight logging only — no
special reconciliation needed since lease renewal handles recovery automatically.

---

### 3. Longer Default Lease (1h → 4h)

**What**: Change `lease_duration_seconds=3600` to `lease_duration_seconds=14400` in
`octopoid/tasks.py`.

**Pros:**
- Trivial one-liner change
- Immediate improvement: most laptop sleeps < 4h won't trigger expiry at all
- Gives more buffer for lease renewal job to kick in

**Cons:**
- Coarse: if an agent dies cleanly, its task stays `claimed` for up to 4h before expiry cleanup
- Does not prevent expiry for very long sleeps or very long tasks

**Assessment:** Simple and directly beneficial. Implemented as a complement to lease renewal.

---

### 4. Graceful Submit Retry — Re-Claim on 409

**What**: When `sdk.tasks.submit()` returns 409 (lease expired), attempt to re-claim the
task and re-submit.

**Pros:**
- Handles the "agent completed but lease expired" case

**Cons:**
- Racing with `check_and_requeue_expired_leases()`: by the time agent tries to re-claim, the
  scheduler may have already requeued it to `incoming` and another agent claimed it
- Agent has no way to know if its commits are already on the branch (partial work)
- Complex error recovery that could leave tasks in inconsistent state
- Much harder to implement and test correctly

**Assessment:** Not recommended. The race conditions make this fragile. With lease renewal
preventing the 409 from occurring in the first place, this is not needed.

---

## Chosen Implementation

**Primary (high impact, low risk):**
1. **`renew_active_leases()`** — new scheduler job that extends leases for alive agents
2. **4h default lease** — buffer to reduce frequency of needed renewals

**Secondary (observability):**
3. **Sleep detection logging** — track `last_tick` in scheduler state, log when gap > 5min

### Lease Renewal Design

```
renew_active_leases():
  1. Fetch all tasks in `claimed` queue
  2. For each task:
     a. If no lease_expires_at → skip
     b. If lease expires more than 30 min from now → skip (plenty of time)
     c. Look up live PID for task (find_pid_for_task returns alive PIDs only)
     d. If no live PID → skip (check_and_requeue_expired_leases will handle it)
     e. If live PID → extend lease by 1 hour (sdk.tasks.update(lease_expires_at=new_ts))
     f. Per-task errors are swallowed to avoid aborting other renewals
```

### Job Order (critical)

`renew_active_leases` MUST run BEFORE `check_and_requeue_expired_leases` in the same tick:
- On post-sleep tick: both jobs are due (long gap since last run)
- `renew_active_leases` extends lease for alive processes
- `check_and_requeue_expired_leases` sees non-expired leases → skips those tasks ✅
- YAML order determines execution order for `remote` group jobs

### Renewal Threshold: 30 minutes

Why 30 minutes:
- `check_and_requeue_expired_leases` runs every 60s — catches actual expiry well within window
- 30 min gives buffer for network latency, slow scheduler ticks, and brief sleep gaps
- Large enough to avoid unnecessary renewals on healthy fast tasks

### Renewal Duration: 1 hour

Why 1 hour:
- Matches the original default lease duration
- Provides enough buffer after a typical sleep/wake cycle
- Not so long that hung processes hold tasks forever

---

## Acceptance Criteria

- [x] `renew_active_leases()` implemented in `octopoid/scheduler.py`
- [x] `renew_active_leases` registered in `octopoid/jobs.py` and `jobs.yaml`
- [x] `renew_active_leases` appears before `check_and_requeue_expired_leases` in YAML
- [x] Default lease extended from 3600s to 14400s (4h) in `octopoid/tasks.py`
- [x] Sleep detection logging added to `run_scheduler()`
- [x] All existing `test_lease_expiry.py` tests pass (including `TestRenewActiveLeases`)
