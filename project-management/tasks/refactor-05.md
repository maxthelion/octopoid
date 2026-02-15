# refactor-05: Wire up scheduler pipeline (replace run_scheduler body)

ROLE: implement
PRIORITY: P1
BRANCH: feature/client-server-architecture
CREATED: 2026-02-15T00:00:00Z
CREATED_BY: human
SKIP_PR: true
DEPENDS_ON: refactor-01, refactor-02, refactor-03, refactor-04

## Context

Tasks refactor-01 through refactor-04 created all the building blocks:
- `AgentContext` dataclass (refactor-01)
- Guard functions + `evaluate_agent()` (refactor-02)
- `HOUSEKEEPING_JOBS` + `run_housekeeping()` (refactor-03)
- Spawn strategies + `get_spawn_strategy()` + `_requeue_task()` (refactor-04)

Now we replace the body of `run_scheduler()` with the pipeline architecture. The old ~270-line function body becomes ~30 lines.

**CRITICAL:** Behaviour must be identical to the old implementation. No new features, no logic changes. Just restructuring. Compare debug logs before and after.

Reference: `project-management/drafts/10-2026-02-15-scheduler-refactor.md` ("Putting It Together" section)

## What to do

### Replace run_scheduler() body

The current `run_scheduler()` (lines 1615-1883) should be replaced with:

```python
def run_scheduler() -> None:
    """Main scheduler loop - evaluate and spawn agents."""
    print(f"[{datetime.now().isoformat()}] Scheduler starting")
    debug_log("Scheduler tick starting")

    # Check global pause flag
    if is_system_paused():
        print("System is paused (rm .octopoid/PAUSE or set 'paused: false' in agents.yaml)")
        debug_log("System is paused globally")
        return

    # Phase 1: Housekeeping
    run_housekeeping()

    # Phase 2 + 3: Evaluate and spawn agents
    try:
        agents = get_agents()
        debug_log(f"Loaded {len(agents)} agents from config")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        debug_log(f"Failed to load agents config: {e}")
        sys.exit(1)

    if not agents:
        print("No agents configured in agents.yaml")
        debug_log("No agents configured")
        return

    for agent_config in agents:
        agent_name = agent_config.get("name")
        role = agent_config.get("role")
        if not agent_name or not role:
            print(f"Skipping invalid agent config: {agent_config}")
            debug_log(f"Invalid agent config: {agent_config}")
            continue

        debug_log(f"Evaluating agent {agent_name}: role={role}")

        # Acquire agent lock
        agent_lock_path = get_agent_lock_path(agent_name)
        with locked_or_skip(agent_lock_path) as acquired:
            if not acquired:
                print(f"Agent {agent_name} is locked (another instance running?)")
                debug_log(f"Agent {agent_name} lock not acquired")
                continue

            # Build context
            state_path = get_agent_state_path(agent_name)
            ctx = AgentContext(
                agent_config=agent_config,
                agent_name=agent_name,
                role=role,
                interval=agent_config.get("interval_seconds", 300),
                state=load_state(state_path),
                state_path=state_path,
            )

            # Evaluate guards
            if not evaluate_agent(ctx):
                continue

            # Spawn
            print(f"[{datetime.now().isoformat()}] Starting agent {agent_name} (role: {role})")
            debug_log(f"Starting agent {agent_name} (role: {role})")

            strategy = get_spawn_strategy(ctx)
            try:
                pid = strategy(ctx)
                print(f"Agent {agent_name} started with PID {pid}")
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] Spawn failed for {agent_name}: {e}")
                debug_log(f"Spawn failed for {agent_name}: {e}")
                if ctx.claimed_task:
                    _requeue_task(ctx.claimed_task["id"])

    print(f"[{datetime.now().isoformat()}] Scheduler tick complete")
    debug_log("Scheduler tick complete")
```

### What to remove

Delete the old inline code that was replaced:
- The inline housekeeping calls (lines 1620-1661) -- now in `run_housekeeping()`
- The inline guard checks (lines 1688-1763) -- now in `evaluate_agent()`
- The inline spawn branches (lines 1770-1880) -- now in spawn strategies

### Behaviour preservation checklist

Make sure these edge cases are preserved:
1. `_register_orchestrator` now runs inside `run_housekeeping()` (after the pause check, not before). This is intentional per the design doc.
2. The `should_trigger_queue_manager()` call (lines 1657-1661) was diagnostic-only. It can be dropped -- the agent's pre-check handles triggering. If you want to keep it, add it as a job in `HOUSEKEEPING_JOBS`.
3. The `agent_id` used for `write_agent_env()` and `spawn_agent()` was the loop enumeration index. In the new code, spawn strategies use `ctx.agent_config.get("id", 0)` instead. Verify this matches.
4. Lock acquisition still wraps both evaluation and spawn (same as before).
5. State loading happens inside the lock (same as before).

## How to verify

1. Run `pytest tests/` -- all tests must pass
2. Run the scheduler with `--debug --once`:
   ```
   python -m orchestrator.scheduler --debug --once
   ```
3. Check the debug log in `.octopoid/runtime/logs/scheduler-*.log`:
   - Housekeeping jobs should all run
   - Guard chain messages should appear for each agent
   - No Python tracebacks

## Key files

- `orchestrator/scheduler.py` -- replace `run_scheduler()` body
- `project-management/drafts/10-2026-02-15-scheduler-refactor.md` -- design reference

## Acceptance criteria

- [ ] `run_scheduler()` is ~30-50 lines using the pipeline (housekeeping -> evaluate -> spawn)
- [ ] Old inline guard/spawn code is removed from `run_scheduler()`
- [ ] Behaviour is identical to the old implementation
- [ ] All existing tests pass (`pytest tests/`)
- [ ] Scheduler runs cleanly with `--debug --once` (no tracebacks)
- [ ] Debug logs show housekeeping jobs, guard evaluations, and spawn decisions
- [ ] No regressions in scheduler functionality
