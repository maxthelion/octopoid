# Laptop sleep resilience: lease expiry, session death, submit 409s

**Captured:** 2026-03-02
**Task:** d968986b

## Problem

When a laptop sleeps during agent execution, three things break:

1. **Lease expires** (default 1h) while agent is suspended — scheduler's `check_and_requeue_expired_leases` kills the process and requeues the task on next wake, discarding work-in-progress.
2. **Agent CLI session may die** — if the suspend is long enough, the network connection to Claude API may timeout on wake, causing the agent to exit with an error.
3. **Submit 409** — if the agent wakes and completes work, but the lease already expired, `sdk.tasks.submit()` returns 409 (task is no longer claimed by this orchestrator). Work is lost.

## Code Context

- `octopoid/tasks.py:66` — `lease_duration_seconds=3600` (1 hour, comment says "no renewal mechanism yet")
- `octopoid/scheduler.py:1963` — `check_and_requeue_expired_leases()` — kills orphan PIDs and requeues tasks with expired leases
- `octopoid/scheduler.py:2329` — `HOUSEKEEPING_JOBS` — `check_and_requeue_expired_leases` runs BEFORE `check_and_update_finished_agents`
- `octopoid/pool.py:183` — `find_pid_for_task(task_id)` — returns `(pid, blueprint_name)` if process is alive, `None` if not

## Approaches

### Option 1: Lease Renewal in Scheduler (RECOMMENDED)

Add a `renew_active_leases()` function to `HOUSEKEEPING_JOBS` that runs **before** `check_and_requeue_expired_leases`. It:
1. Lists tasks in `claimed` queue
2. For each, checks if a process is still running via `find_pid_for_task()`
3. If process is alive AND lease is about to expire or already expired, renews the lease (extends by 1h)

**Why this works for sleep:** When the laptop wakes, the scheduler runs its next tick. `renew_active_leases` runs first, finds suspended-but-alive agent processes, and extends their leases before `check_and_requeue_expired_leases` has a chance to kill them.

**Pros:**
- No changes to agent code
- Handles leases for all agent types uniformly
- Works even if the agent is fully suspended (scheduler renews on its behalf)
- Cheap: only calls the API for tasks with expiring leases

**Cons:**
- Scheduler itself must wake before the lease expires by more than one tick (60 seconds). In practice a laptop sleep pauses all processes simultaneously, so they all resume at once — this is fine.
- Doesn't help if the scheduler is down independently of the laptop

**Threshold for renewal:** Renew when `lease_expires_at` is within 30 minutes of expiry (or already past). This gives a comfortable window for normal sleep durations while not spamming renewals every tick.

---

### Option 2: Sleep Detection via Heartbeat Gap

The scheduler sends a heartbeat every 60 seconds. After sleep, the gap between the last heartbeat and now will be much larger than 60 seconds.

Add a `detect_and_handle_wake_from_sleep()` job that:
1. Compares current time to last heartbeat timestamp
2. If gap > 5 minutes → "probably woke from sleep"
3. Scans claimed tasks with alive processes and renews their leases

**Pros:**
- Explicit sleep detection, could trigger other recovery actions

**Cons:**
- More complex than Option 1
- "Gap > threshold" is a heuristic — can produce false positives if scheduler is just slow
- Heartbeat is per-orchestrator, not per-process — still need `find_pid_for_task`
- Adds a stateful dependency (last heartbeat file)

---

### Option 3: Longer Default Lease (4h instead of 1h)

Simply change `lease_duration_seconds=3600` to `lease_duration_seconds=14400`.

**Pros:**
- Trivial to implement
- Handles most real-world sleeps (< 4h)

**Cons:**
- Coarse: dead agents aren't recycled for 4 hours
- Doesn't help for long overnight sleeps
- Makes the system less responsive to genuinely dead agents

---

### Option 4: Graceful Submit Retry on 409

In the submit/completion flow, if a 409 is returned (lease expired), attempt to re-claim the task and then re-submit.

**Pros:**
- Handles the submit case cleanly at the point of failure

**Cons:**
- Re-claiming might fail if the task has been re-assigned (race condition)
- Doesn't prevent the task from being requeued between expiry and re-submit
- Complex: need to detect "my task" vs "someone else's task" 409

---

## Decision

**Implement Option 1 (lease renewal in scheduler) as the primary fix.**

This covers the most common case (laptop sleeps for < 4 hours while agents work) with minimal complexity, no agent changes, and no heuristics. It's also the building block that makes Options 2 and 4 unnecessary.

## Implementation Plan

1. Add `renew_active_leases()` to `scheduler.py` (near `check_and_requeue_expired_leases`)
2. Insert it into `HOUSEKEEPING_JOBS` BEFORE `check_and_requeue_expired_leases`
3. Update the comment in `tasks.py` to reflect that renewal now exists
4. Add a test for the renewal behavior

## Invariants

- `active-lease-renewed`: A task in `claimed` queue with a live agent process will have its lease extended before it expires. The lease expiry check will never kill a process that is still tracked in `running_pids.json`.
- `sleep-transparent`: A laptop sleep of up to N hours (where N * 60 > renewal_threshold in minutes) will not cause task requeue if the agent process survived the sleep.

## Open Questions

- Should we also bump the initial `lease_duration_seconds` to something higher as a belt-and-suspenders? (e.g. 2h instead of 1h)
- Should renewal be logged as an `info` message or `debug`? (Currently: `info` so it's visible in scheduler logs)
