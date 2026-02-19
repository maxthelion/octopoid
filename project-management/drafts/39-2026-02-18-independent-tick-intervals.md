# Independent Tick Intervals for Scheduler Concerns

**Status:** Analysed
**Captured:** 2026-02-18
**Updated:** 2026-02-19

## Problem

The scheduler runs all concerns on a single tick interval (currently 60s via launchd). This creates two problems:

1. **Rate limit budget.** Cloudflare Workers free tier = 10,000 requests/day. Current API calls per tick: ~11 (1 register + 1 provisional list + 3×3 agent guard calls). At 60s = 660/hr = **15,840/day — already over budget.** And that's with nothing happening; active spawns add more.

2. **Responsiveness.** Local operations (detecting finished agents, reading result.json) are delayed by the same interval that throttles remote calls. A gatekeeper finishes but the scheduler doesn't notice for 60s.

## API Call Breakdown Per Tick (actual, worse than first estimated)

| Job | Calls | Notes |
|-----|-------|-------|
| `_register_orchestrator` | 1 | POST every tick (idempotent) |
| `check_and_update_finished_agents` | 0-3 | 0 normally (local PID check), 1-3 on flow dispatch |
| `process_orchestrator_hooks` | 1+ | Lists provisional, then 1-2 per task with hooks |
| `_check_queue_health_throttled` | 0 | Self-throttled to 30min, ~5 when it runs |
| Per-agent `guard_backpressure` | **3-4** | `count_queue("incoming")` + `can_claim_task()` which calls `count_queue` for incoming, claimed, AND provisional — each is a full `sdk.tasks.list()` |
| Per-agent `guard_claim_task` | 1 | Claim attempt (only if backpressure passes) |
| **Total (3 agents, idle)** | **~14-16** | Backpressure alone: up to 12 list calls (4 × 3 agents) |

At 60s interval: **~960/hr = 23,000/day.** Well over the 10,000 limit.

## Proposed Solution: Layered Intervals + Activity Scaling

### Layer 1: Per-job intervals

Each housekeeping job and the agent evaluation loop gets its own interval. Store `last_run` timestamps in `.octopoid/runtime/scheduler_state.json`. Launchd ticks every 10s but each job skips if not due.

| Job | Interval | Rationale |
|-----|----------|-----------|
| `check_and_update_finished_agents` | 10s | Local only (PID checks). API calls only when result found. |
| `_register_orchestrator` | 300s (5min) | Idempotent, no urgency |
| `process_orchestrator_hooks` | 60s | Needs server, but only runs if provisional queue non-empty |
| `_check_queue_health_throttled` | 1800s (30min) | Already self-throttled, keep as-is |
| Agent evaluation loop | 60s | The main API consumer |

**Projected idle budget:** register 1×(86400/300) = 288 + hooks 1×(86400/60) = 1440 + agents 11×(86400/60) = wait, need to recalculate with per-agent intervals too. Key point: register alone drops from 1440/day to 288/day.

### Layer 2: Activity-aware scaling

Track "last user activity" — defined as:
- Last time a task was enqueued (check incoming queue count change)
- Last time an agent finished (result.json appeared)
- Last time the user ran a CLI command (touch a sentinel file from skills/CLI)

Scale the agent evaluation interval based on activity:
- **Active (activity within 10min):** evaluate every 30s
- **Idle (10min-1hr):** evaluate every 120s
- **Dormant (>1hr, or overnight 00:00-07:00):** evaluate every 300s

This means overnight with no activity: ~3 API calls per 5-min tick = 36/hr = **~250 overnight (8hr)**. During active work: ~11 per 30s tick = 1320/hr, but only for a few hours.

### Layer 3: Smart short-circuit in guards

Most API calls come from the guard chain running for each agent even when there's nothing to do. Add a shared cache:
- `guard_backpressure` calls `count_queue("incoming")` per agent. Cache the result for the tick — one API call instead of three.
- `_register_orchestrator` should check a local flag and skip if already registered this session.

### Budget estimate (24hr active day)

| Period | Hours | Ticks/hr | Calls/tick | Total |
|--------|-------|----------|------------|-------|
| Active work | 8 | 120 (30s) | ~5 (cached) | 4,800 |
| Idle | 8 | 30 (120s) | ~5 | 1,200 |
| Dormant/overnight | 8 | 12 (300s) | ~3 | 288 |
| **Total** | | | | **~6,300** |

Comfortably under 10,000.

## Implementation Plan

### Step 1: Per-job intervals (simplest, biggest win)
- Add `scheduler_state.json` with per-job `last_run` timestamps
- Wrap each housekeeping job and agent eval loop with interval check
- Reduce launchd interval to 10s
- Cache `count_queue` result within a single tick

### Step 2: Activity-aware scaling
- Add activity sentinel file (`.octopoid/runtime/last_activity`)
- Touch it from CLI commands, enqueue, agent finish
- Read in scheduler to determine active/idle/dormant mode
- Scale agent eval interval accordingly

### Step 3: Time-of-day override
- Add `quiet_hours` config to agents.yaml (default: 00:00-07:00)
- Force dormant mode during quiet hours regardless of activity

## Alternative: Server-Side Summary Endpoint

Instead of reducing the number of ticks, reduce the number of calls *per tick*. The root problem is that the scheduler makes ~14 separate API calls to build a picture of queue state that the server already knows.

### Option A: `/api/v1/scheduler/poll` summary endpoint

One GET call returns everything the scheduler needs for a tick:

```json
{
  "queue_counts": { "incoming": 4, "claimed": 2, "provisional": 1, "done": 50, "failed": 3 },
  "claimable": true,
  "provisional_tasks": [
    { "id": "TASK-abc", "hooks": [...], "pr_number": 87 }
  ],
  "orchestrator_registered": true
}
```

The scheduler calls this once per tick. Backpressure is evaluated from `queue_counts` (no extra calls). `provisional_tasks` replaces the separate list call in `process_orchestrator_hooks`. Registration is confirmed without a separate POST.

**Budget:** 1 call/tick × 1440 ticks/day (60s interval) = **1,440/day**. Add write calls (claim, reject, accept — maybe 20-50/day) = **~1,500/day total**. Massive headroom.

Claim still needs a separate POST (it's a write operation), but everything else collapses into one read.

### Option B: EventSource (SSE) push model

Server pushes events to the scheduler over a persistent connection:

```
event: task_created
data: {"id": "TASK-abc", "queue": "incoming", "role": "implement"}

event: task_submitted
data: {"id": "TASK-def", "queue": "provisional", "pr_number": 87}
```

Scheduler maintains local state from the event stream. Only makes write calls (claim, reject, accept) when it needs to mutate.

**Pros:** Near-zero polling. Instant responsiveness — scheduler reacts to events as they happen. Budget is essentially just write calls (~50-100/day).

**Cons:** More complex. Needs persistent connection management, reconnection logic, state reconciliation on reconnect. Cloudflare Workers don't natively support long-lived SSE (would need Durable Objects or a different hosting model). Overkill for current scale.

### Option C: Hybrid — summary endpoint + activity sentinel

Combine Option A with the activity-aware scaling from the layered approach:

- `/api/v1/scheduler/poll` returns all read state in one call
- Activity sentinel controls tick frequency (active: 15s, idle: 60s, dormant: 300s)
- Local operations (PID checks) run every tick regardless
- Remote poll only runs on the slower schedule

**Budget:** Active 8hr × 240 polls/hr = 1,920 + idle 8hr × 60/hr = 480 + dormant 8hr × 12/hr = 96 + writes ~100 = **~2,600/day**. Plenty of headroom even with growth.

### Recommendation

**Option C (hybrid)** gives the best balance:
- Simple to implement (one new endpoint + interval logic)
- Massive reduction in API calls (14 → 1 per tick)
- Activity-aware scaling adds responsiveness during active use
- No architectural changes needed (no SSE, no Durable Objects)
- Option B (SSE) is a future upgrade path if we outgrow polling

## Implementation Plan (revised)

### Step 1: Summary endpoint (biggest win, ~1 day)
- Add `GET /api/v1/scheduler/poll` to server returning queue counts + provisional tasks + registration status
- Refactor `backpressure.py` to accept queue counts dict instead of calling `count_queue()` per queue
- Refactor `process_orchestrator_hooks` to accept provisional task list from poll response
- Make `_register_orchestrator` a no-op if poll confirms registration
- **Result:** 14 calls/tick → 1-2 calls/tick

### Step 2: Per-job intervals + fast local loop (~0.5 day)
- Reduce launchd to 10s
- `check_and_update_finished_agents` runs every tick (local only)
- Remote poll runs on its own interval (default 60s)
- Store `last_run` in `scheduler_state.json`

### Step 3: Activity-aware scaling (~0.5 day)
- Touch `.octopoid/runtime/last_activity` from CLI/skills
- Scheduler reads it to pick active/idle/dormant mode
- Scale remote poll interval accordingly

## Open Questions

- Should we add a request counter to the SDK so we can monitor actual usage?
- Should dormant mode skip agent evaluation entirely, or just slow it down?
- Should the poll endpoint also return "suggested next poll interval" so the server can signal backoff?
