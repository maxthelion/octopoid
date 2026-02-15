# Scheduler Refactor: Pipeline Architecture

## Problem

`scheduler.py` is 1974 lines with 50+ functions. The main loop `run_scheduler()` does 10 different jobs in sequence, then enters a per-agent loop with 7 layers of nested guards (paused → locked → running → crashed → overdue → backpressure → pre-check → claim → spawn). Adding a new guard or agent type means wedging into a deeply nested if/continue chain.

The function is hard to reason about because:
- **Mixed concerns:** Housekeeping (finished agents, queue health, hooks) and agent evaluation live in the same function
- **Guard repetition:** Each guard (paused, locked, running, interval) uses the same pattern: check, log, continue. But because they're inline, you can't see the pipeline at a glance
- **Spawn logic is role-specific but inline:** Implementers take one path (prepare_task_directory + invoke_claude), lightweight agents skip worktree, orchestrator_impl agents init submodules — all crammed into the same for-loop body
- **No clear extension point:** Adding a new agent type (like the gatekeeper from draft #7) means adding another `if role == "..."` branch to an already long function

## Proposed Architecture

Split `run_scheduler()` into three clean layers:

```
run_scheduler()
  ├── Phase 1: Housekeeping     (independent jobs, run every tick)
  ├── Phase 2: Agent evaluation  (filter chain per agent)
  └── Phase 3: Spawn            (per-type strategy)
```

### Phase 1: Housekeeping

These are independent jobs that run every tick. They don't interact with each other and don't depend on agent evaluation. Extract each into its own function (most already are) and call them from a list:

```python
HOUSEKEEPING_JOBS = [
    _register_orchestrator,
    check_and_update_finished_agents,
    _check_queue_health_throttled,
    process_orchestrator_hooks,
    process_auto_accept_tasks,
    assign_qa_checks,
    process_gatekeeper_reviews,
    dispatch_gatekeeper_agents,
    check_stale_branches,
    check_branch_freshness,
]

def run_housekeeping():
    """Run all housekeeping jobs. Each is independent and fault-isolated."""
    for job in HOUSEKEEPING_JOBS:
        try:
            job()
        except Exception as e:
            debug_log(f"Housekeeping job {job.__name__} failed: {e}")
```

Benefits:
- Adding a new housekeeping job = append to list
- One failing job doesn't kill the tick
- Easy to see what runs every tick at a glance

### Phase 2: Agent Evaluation (Filter Chain)

Replace the nested if/continue guards with a filter chain. Each guard is a function that returns `(proceed: bool, reason: str)`. The chain stops at the first `proceed=False`.

```python
@dataclass
class AgentContext:
    """Everything the filter chain needs to evaluate an agent."""
    agent_config: dict
    agent_name: str
    role: str
    interval: int
    state: AgentState
    state_path: Path
    claimed_task: dict | None = None

def guard_enabled(ctx: AgentContext) -> tuple[bool, str]:
    if ctx.agent_config.get("paused", False):
        return False, "paused"
    return True, ""

def guard_not_running(ctx: AgentContext) -> tuple[bool, str]:
    if ctx.state.running and ctx.state.pid and is_process_running(ctx.state.pid):
        return False, f"still running (PID {ctx.state.pid})"
    # If marked running but dead, clean up
    if ctx.state.running:
        ctx.state = mark_finished(ctx.state, 1)
        save_state(ctx.state, ctx.state_path)
    return True, ""

def guard_interval(ctx: AgentContext) -> tuple[bool, str]:
    if not is_overdue(ctx.state, ctx.interval):
        return False, "not due yet"
    return True, ""

def guard_backpressure(ctx: AgentContext) -> tuple[bool, str]:
    can_proceed, reason = check_backpressure_for_role(ctx.role)
    if not can_proceed:
        ctx.state.extra["blocked_reason"] = reason
        ctx.state.extra["blocked_at"] = datetime.now().isoformat()
        save_state(ctx.state, ctx.state_path)
        return False, f"backpressure: {reason}"
    # Clear previous block
    ctx.state.extra.pop("blocked_reason", None)
    ctx.state.extra.pop("blocked_at", None)
    return True, ""

def guard_pre_check(ctx: AgentContext) -> tuple[bool, str]:
    if not run_pre_check(ctx.agent_name, ctx.agent_config):
        return False, "pre-check: no work"
    return True, ""

def guard_claim_task(ctx: AgentContext) -> tuple[bool, str]:
    """For claimable roles, try to claim a task. Populates ctx.claimed_task."""
    if ctx.role not in CLAIMABLE_AGENT_ROLES:
        return True, ""
    task_role = AGENT_TASK_ROLE[ctx.role]
    allowed_types = ctx.agent_config.get("allowed_task_types")
    type_filter = _resolve_type_filter(allowed_types)
    ctx.claimed_task = claim_and_prepare_task(ctx.agent_name, task_role, type_filter=type_filter)
    if ctx.claimed_task is None:
        return False, "no task available"
    return True, ""

AGENT_GUARDS = [
    guard_enabled,
    guard_not_running,
    guard_interval,
    guard_backpressure,
    guard_pre_check,
    guard_claim_task,
]

def evaluate_agent(ctx: AgentContext) -> bool:
    """Run the guard chain. Returns True if agent should be spawned."""
    for guard in AGENT_GUARDS:
        proceed, reason = guard(ctx)
        if not proceed:
            debug_log(f"Agent {ctx.agent_name}: blocked by {guard.__name__}: {reason}")
            return False
    return True
```

Benefits:
- Each guard is independently testable
- Adding a new guard = write a function, append to list
- The evaluation logic reads top-to-bottom with no nesting
- Guards can be reordered or conditionally included per agent type

### Phase 3: Spawn (Per-Type Strategy)

Instead of `if role == "implementer" ... elif not is_lightweight ...` inline, define a spawn strategy per agent type. This is where agent directories (draft #9) plug in naturally.

```python
def spawn_implementer(ctx: AgentContext) -> int:
    """Spawn an implementer: prepare task dir, invoke claude directly."""
    task_dir = prepare_task_directory(ctx.claimed_task, ctx.agent_name, ctx.agent_config)
    pid = invoke_claude(task_dir, ctx.agent_config)

    new_state = mark_started(ctx.state, pid)
    new_state.extra["agent_mode"] = "scripts"
    new_state.extra["task_dir"] = str(task_dir)
    new_state.extra["current_task_id"] = ctx.claimed_task["id"]
    save_state(new_state, ctx.state_path)
    return pid

def spawn_lightweight(ctx: AgentContext) -> int:
    """Spawn a lightweight agent (no worktree, runs in parent project)."""
    write_agent_env(ctx.agent_name, ctx.agent_config.get("id", 0), ctx.role, ctx.agent_config)
    pid = spawn_agent(ctx.agent_name, ctx.agent_config.get("id", 0), ctx.role, ctx.agent_config)
    new_state = mark_started(ctx.state, pid)
    save_state(new_state, ctx.state_path)
    return pid

def spawn_worktree(ctx: AgentContext) -> int:
    """Spawn an agent with a worktree (general case for non-lightweight, non-implementer)."""
    base_branch = _resolve_base_branch(ctx)
    ensure_worktree(ctx.agent_name, base_branch)

    if ctx.role == "orchestrator_impl":
        _init_submodule(ctx.agent_name)

    setup_agent_commands(ctx.agent_name, ctx.role)
    generate_agent_instructions(ctx.agent_name, ctx.role, ctx.agent_config)
    write_agent_env(ctx.agent_name, ctx.agent_config.get("id", 0), ctx.role, ctx.agent_config)
    pid = spawn_agent(ctx.agent_name, ctx.agent_config.get("id", 0), ctx.role, ctx.agent_config)

    new_state = mark_started(ctx.state, pid)
    save_state(new_state, ctx.state_path)
    return pid

def get_spawn_strategy(ctx: AgentContext):
    """Select spawn strategy based on agent type."""
    if ctx.role == "implementer" and ctx.claimed_task:
        return spawn_implementer
    if ctx.agent_config.get("lightweight", False):
        return spawn_lightweight
    return spawn_worktree
```

With agent directories (draft #9), `get_spawn_strategy` could read from the agent directory's `agent.yaml` instead of hardcoding role names. Each agent type would declare its own spawn mode.

### Putting It Together

```python
def run_scheduler() -> None:
    """Main scheduler loop."""
    print(f"[{datetime.now().isoformat()}] Scheduler starting")

    if is_system_paused():
        print("System is paused")
        return

    # Phase 1: Housekeeping
    run_housekeeping()

    # Phase 2 + 3: Evaluate and spawn agents
    agents = get_agents()
    for agent_config in agents:
        agent_name = agent_config.get("name")
        role = agent_config.get("role")
        if not agent_name or not role:
            continue

        agent_lock_path = get_agent_lock_path(agent_name)
        with locked_or_skip(agent_lock_path) as acquired:
            if not acquired:
                continue

            ctx = AgentContext(
                agent_config=agent_config,
                agent_name=agent_name,
                role=role,
                interval=agent_config.get("interval_seconds", 300),
                state=load_state(get_agent_state_path(agent_name)),
                state_path=get_agent_state_path(agent_name),
            )

            if not evaluate_agent(ctx):
                continue

            strategy = get_spawn_strategy(ctx)
            try:
                pid = strategy(ctx)
                print(f"[{datetime.now().isoformat()}] Started {agent_name} (PID {pid})")
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] Spawn failed for {agent_name}: {e}")
                if ctx.claimed_task:
                    _requeue_task(ctx.claimed_task["id"])
```

The main function is now ~30 lines instead of ~270. The structure is immediately obvious.

## Migration Path

This is a refactor, not a rewrite. Every function already exists — we're just reorganising how they're called.

1. **Extract `AgentContext` dataclass** — holds all the per-agent state that currently lives in local variables
2. **Extract guard functions** — each `if ... continue` block becomes a guard function. Logic stays identical.
3. **Extract spawn strategies** — each `if role == ...` branch becomes a strategy function. Logic stays identical.
4. **Wire up the pipeline** — replace the inline code in `run_scheduler()` with the pipeline
5. **Test** — run the scheduler with `--debug` and compare logs to before. Behaviour should be identical.

No behaviour changes. No new features. Just structure.

## Connection to Agent Directories (Draft #9)

With agent directories, the spawn strategy is defined by the agent type, not hardcoded in the scheduler:

```yaml
# agents/implementer/agent.yaml
spawn_mode: scripts       # prepare_task_directory + invoke_claude
lightweight: false
```

```yaml
# agents/github-issue-monitor/agent.yaml
spawn_mode: module        # python -m <module>
lightweight: true
```

The scheduler reads `spawn_mode` from the agent directory and dispatches accordingly. Adding a new agent type with a new spawn mode doesn't touch the scheduler at all.

## What This Enables

1. **Testable guards:** Each guard function can be unit tested in isolation
2. **Pluggable spawn:** New agent types don't modify the scheduler's main loop
3. **Visible pipeline:** The scheduler's structure is obvious from `run_scheduler()` — housekeeping, evaluate, spawn
4. **Fault isolation:** A failing housekeeping job or spawn doesn't crash the whole tick
5. **Agent directories:** The spawn strategy naturally maps to agent type config

## Estimated Scope

- ~200 lines of new structural code (dataclass, guard functions, spawn strategies, pipeline)
- ~270 lines removed from `run_scheduler()` (moved into the above)
- Net change: roughly neutral line count, but dramatically improved readability
- All existing functions stay — `prepare_task_directory`, `invoke_claude`, `spawn_agent`, `ensure_worktree`, etc. are unchanged
- The helpers remain helpers. Only the wiring changes.

## Open Questions

1. **Should guards be configurable per agent type?** Some agents might not need backpressure checks (e.g. lightweight self-management agents). Could skip guards based on agent config or agent directory settings.

2. **Should housekeeping jobs have their own intervals?** Currently some have ad-hoc throttling (`_check_queue_health_throttled` runs every 30 minutes). Could formalise this with a `last_run` timestamp per job.

3. **Locking:** The agent lock currently wraps both evaluation and spawn. Should it only wrap spawn? Evaluation is read-only except for state updates in guard_not_running and guard_backpressure.
