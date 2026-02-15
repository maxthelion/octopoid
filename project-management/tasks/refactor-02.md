# refactor-02: Extract guard functions from scheduler agent loop

ROLE: implement
PRIORITY: P1
BRANCH: feature/client-server-architecture
CREATED: 2026-02-15T00:00:00Z
CREATED_BY: human
SKIP_PR: true
DEPENDS_ON: refactor-01

## Context

The `run_scheduler()` agent for-loop (lines 1677-1882 of `orchestrator/scheduler.py`) has 7 layers of nested `if/continue` guards: paused, locked, running, crashed, overdue, backpressure, pre-check, claim task. Each guard checks a condition, logs a message, and `continue`s to the next agent. This nesting makes the function hard to read and impossible to unit test individual guards.

This task extracts each guard into a standalone function that takes an `AgentContext` (from refactor-01) and returns `tuple[bool, str]` -- `(should_proceed, reason_if_blocked)`. It also creates an `AGENT_GUARDS` list and an `evaluate_agent()` function that runs the chain.

This is a prep step -- we create the functions alongside `run_scheduler()` but do NOT modify `run_scheduler()` itself. Task refactor-05 will wire them up.

Reference: `project-management/drafts/10-2026-02-15-scheduler-refactor.md` (Phase 2: Agent Evaluation)

## What to do

Add the following functions to `orchestrator/scheduler.py`, placed AFTER the `AgentContext` dataclass (from refactor-01) and BEFORE `run_scheduler()`:

### Guard functions

Each guard takes `ctx: AgentContext` and returns `tuple[bool, str]`:

1. **`guard_enabled(ctx)`** -- Check if agent is paused
   - If `ctx.agent_config.get("paused", False)` is True, return `(False, "paused")`
   - Otherwise return `(True, "")`

2. **`guard_not_running(ctx)`** -- Check if agent process is still running; clean up crashed agents
   - If `ctx.state.running` and `ctx.state.pid` and `is_process_running(ctx.state.pid)`: return `(False, f"still running (PID {ctx.state.pid})")`
   - If `ctx.state.running` but process is dead: call `mark_finished(ctx.state, 1)`, save state, update `ctx.state` -- then return `(True, "")`
   - Otherwise return `(True, "")`
   - References: `is_process_running()`, `mark_finished()`, `save_state()` -- all already imported

3. **`guard_interval(ctx)`** -- Check if agent is due to run
   - If `not is_overdue(ctx.state, ctx.interval)`: return `(False, "not due yet")`
   - Otherwise return `(True, "")`
   - Reference: `is_overdue()` already imported

4. **`guard_backpressure(ctx)`** -- Check role-based backpressure
   - Call `check_backpressure_for_role(ctx.role)` -- returns `(can_proceed, reason)`
   - If blocked: set `ctx.state.extra["blocked_reason"]` and `ctx.state.extra["blocked_at"]`, save state, return `(False, f"backpressure: {reason}")`
   - If proceeding: clear `blocked_reason` and `blocked_at` from `ctx.state.extra` (use `.pop()` to handle missing keys), return `(True, "")`
   - Reference: `check_backpressure_for_role()` imported from `.backpressure`

5. **`guard_pre_check(ctx)`** -- Run pre-check for work availability
   - If `not run_pre_check(ctx.agent_name, ctx.agent_config)`: return `(False, "pre-check: no work")`
   - Otherwise return `(True, "")`
   - Reference: `run_pre_check()` defined at line 81 of scheduler.py

6. **`guard_claim_task(ctx)`** -- For claimable roles, claim a task
   - If `ctx.role not in CLAIMABLE_AGENT_ROLES`: return `(True, "")` (non-claimable agents skip this)
   - Get `task_role = AGENT_TASK_ROLE[ctx.role]`
   - Get `allowed_types = ctx.agent_config.get("allowed_task_types")`
   - Resolve type filter: `_resolve_type_filter(allowed_types)` -- extract this helper from the inline logic at lines 1757-1759:
     ```python
     def _resolve_type_filter(allowed_types):
         if isinstance(allowed_types, list) and len(allowed_types) == 1:
             return allowed_types[0]
         if isinstance(allowed_types, list):
             return ",".join(allowed_types)
         return allowed_types
     ```
   - Call `claim_and_prepare_task(ctx.agent_name, task_role, type_filter=type_filter)`
   - Set `ctx.claimed_task` to the result
   - If `ctx.claimed_task is None`: return `(False, "no task available")`
   - Otherwise return `(True, "")`
   - References: `CLAIMABLE_AGENT_ROLES`, `AGENT_TASK_ROLE` from `.config`, `claim_and_prepare_task()` at line 262

### AGENT_GUARDS list

```python
AGENT_GUARDS = [
    guard_enabled,
    guard_not_running,
    guard_interval,
    guard_backpressure,
    guard_pre_check,
    guard_claim_task,
]
```

Order matters: cheapest checks first, expensive checks (pre_check, claim_task) last.

### evaluate_agent function

```python
def evaluate_agent(ctx: AgentContext) -> bool:
    """Run the guard chain. Returns True if agent should be spawned."""
    for guard in AGENT_GUARDS:
        proceed, reason = guard(ctx)
        if not proceed:
            debug_log(f"Agent {ctx.agent_name}: blocked by {guard.__name__}: {reason}")
            return False
    return True
```

### Also extract `_resolve_type_filter` as a standalone helper

The inline type filter logic at lines 1757-1759 should be extracted into a helper function used by `guard_claim_task`. Place it near `guard_claim_task`.

## What NOT to do

- Do NOT modify `run_scheduler()` -- it keeps its existing inline guards for now
- Do NOT modify any existing functions
- Do NOT change tests

## Key files

- `orchestrator/scheduler.py` -- add guard functions, AGENT_GUARDS, evaluate_agent()
- `orchestrator/config.py` -- contains `CLAIMABLE_AGENT_ROLES`, `AGENT_TASK_ROLE` (already imported)
- `orchestrator/state_utils.py` -- contains `AgentState`, `is_overdue`, `mark_finished`, etc.
- `project-management/drafts/10-2026-02-15-scheduler-refactor.md` -- design reference

## Acceptance criteria

- [ ] All 6 guard functions exist with correct signatures: `(ctx: AgentContext) -> tuple[bool, str]`
- [ ] `_resolve_type_filter()` helper extracted as standalone function
- [ ] `AGENT_GUARDS` list contains all 6 guards in order: enabled, not_running, interval, backpressure, pre_check, claim_task
- [ ] `evaluate_agent(ctx)` runs the chain and stops at first `proceed=False`, logging the blocking guard
- [ ] No changes to `run_scheduler()` or any existing functions
- [ ] All existing tests pass (`pytest tests/`)
- [ ] Guard logic matches the existing inline logic in `run_scheduler()` exactly
