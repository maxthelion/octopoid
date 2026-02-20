# Flow Engine Owns Transitions — Steps Are Pre-Transition Side Effects

**Status:** Idea
**Captured:** 2026-02-20

## Raw

> "Which fits best with the pure function model? It needs to be the same across our flows. No random band aids."

## Problem

The flow YAML declares transitions with targets:

```yaml
"claimed -> provisional":
  runs: [push_branch, run_tests, create_pr, submit_to_server]
```

But the engine doesn't perform the transition. It runs the steps and hopes one of them does it. `submit_to_server` happens to call `sdk.tasks.submit()`, which is what actually moves the task from `claimed` to `provisional`. The flow says the target is `provisional`, but whether the task gets there depends on whether a step happened to call the right API method.

This breaks in two ways:

1. **Child flow has no transition step.** `"claimed -> done": runs: [rebase_on_project_branch, run_tests]` — neither step moves the task. After both succeed, the task stays in `claimed` forever.

2. **Step failure orphans tasks.** If any step throws (rate limit, rebase conflict, test failure), the exception is caught silently, the PID is cleaned up, and the task is stuck in `claimed` with no agent and no way to recover.

Both bugs produce the same symptom: tasks stuck in `claimed` with `no-pid` status. We've been manually rescuing them every hour.

## Design

**The flow engine should own the transition.** Steps are pre-transition side effects — things that need to happen before the task can move. After all steps complete successfully, the engine moves the task to the target queue. The step list should never need to include a "move the task" step.

### Current (broken)

```
_handle_done_outcome:
  1. Load flow, find transition from current queue
  2. execute_steps(transition.runs)  ← steps must include a "move" step
  3. Print success message           ← no actual transition by the engine
```

### Proposed

```
_handle_done_outcome:
  1. Load flow, find transition from current queue
  2. Execute pre-transition steps (transition.runs)
     - If any step fails → handle failure (see below)
  3. Engine performs the transition: sdk.tasks.submit() or sdk.tasks.accept()
     - Target queue comes from the flow YAML, not from the step
  4. Log the transition
```

### Transition method selection

The engine needs to know which API call to make based on the target queue:

| Target | API call | Notes |
|--------|----------|-------|
| `provisional` | `sdk.tasks.submit()` | Standard submit after implementation |
| `done` | `sdk.tasks.accept()` | Direct accept (child tasks, auto-accept) |
| `failed` | `sdk.tasks.update(queue="failed")` | Explicit failure |
| `incoming` | `sdk.tasks.update(queue="incoming")` | Rejection / recycle |
| Custom (e.g. `sanity_approved`) | `sdk.tasks.update(queue=target)` | Extensible queues |

This can be a simple mapping or convention:
- `done` → accept endpoint
- `provisional` → submit endpoint
- Everything else → PATCH with queue

### Step failure handling

When a step fails, the engine should:

1. **Not silently swallow the exception.** Log the error with the step name and task ID.
2. **Not delete the PID from tracking.** Keep it so the next scheduler tick can detect and retry.
3. **Record the failure.** Write a `step_failure` field on the task (step name, error message, timestamp).
4. **Apply a retry policy.** After N consecutive step failures for the same task, move to `failed` instead of retrying forever.

### What `submit_to_server` becomes

It gets removed from step lists. The default flow changes from:

```yaml
"claimed -> provisional":
  runs: [push_branch, run_tests, create_pr, submit_to_server]
```

To:

```yaml
"claimed -> provisional":
  runs: [push_branch, run_tests, create_pr]
```

The engine handles the `submit` call after `create_pr` succeeds. Same for all flows — no flow needs a "move the task" step.

### PID cleanup fix

In `check_and_update_finished_agents`, the PID deletion must be conditional:

```python
for pid, info in dead_pids.items():
    task_id = info.get("task_id", "")
    if task_id:
        try:
            handle_agent_result(task_id, instance_name, task_dir)
            del pids[pid]  # Only remove PID on success
        except Exception:
            # Leave PID in tracking — next tick will retry
            # After N failures, move task to failed and then remove PID
            pass
```

## Impact

- **Fixes child flow orphaning** — the engine transitions to `done` after steps complete
- **Fixes step failure orphaning** — PID stays tracked, task gets retried or moved to failed
- **Simplifies flow authoring** — flows declare target queues, steps are just side effects
- **Enables boxen's custom queues** — transitions to `sanity_approved`, `human_review` etc. work automatically without needing custom steps for each queue

## Scope

| File | Change |
|------|--------|
| `orchestrator/scheduler.py` `_handle_done_outcome` | Engine performs transition after steps |
| `orchestrator/scheduler.py` `check_and_update_finished_agents` | Conditional PID cleanup |
| `orchestrator/steps.py` | Remove `submit_to_server` step (or deprecate) |
| `.octopoid/flows/default.yaml` | Remove `submit_to_server` from step list |
| `.octopoid/flows/project.yaml` | Child flow works as-is (engine handles `claimed -> done`) |
| `orchestrator/scheduler.py` `handle_agent_result` | Don't silently catch exceptions |

## Open Questions

- Should step failures retry in-place (keep in `claimed`, re-run steps on next tick) or requeue to `incoming` (agent runs again from scratch)?
- Should there be a `max_step_retries` config per flow or per transition?
- Should the `submit_to_server` step be kept for backwards compatibility, or removed entirely? If kept, the engine should detect it and skip its own transition call.
