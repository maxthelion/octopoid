# refactor-04: Extract spawn strategies from scheduler

ROLE: implement
PRIORITY: P1
BRANCH: feature/client-server-architecture
CREATED: 2026-02-15T00:00:00Z
CREATED_BY: human
SKIP_PR: true
DEPENDS_ON: refactor-01

## Context

The spawn logic in `run_scheduler()` (lines 1770-1880 of `orchestrator/scheduler.py`) has three different code paths based on agent role and configuration:

1. **Implementer path** (lines 1770-1792): `prepare_task_directory()` + `invoke_claude()`, saves state with `agent_mode=scripts`
2. **Lightweight path** (lines 1866-1878): No worktree, just `write_agent_env()` + `spawn_agent()`
3. **Worktree path** (lines 1797-1878): `ensure_worktree()` + optional submodule init + `setup_agent_commands()` + `generate_agent_instructions()` + `write_agent_env()` + `spawn_agent()`

These paths are interleaved with inline branching (`if role == "implementer"`, `if not is_lightweight`, `if role == "orchestrator_impl"`), making it hard to add new agent types without modifying the main loop.

This task extracts each path into a spawn strategy function that takes an `AgentContext` (from refactor-01) and returns a PID. It also creates `get_spawn_strategy()` to dispatch to the right strategy, and a `_requeue_task()` helper for error recovery.

This is a prep step -- we create the functions alongside `run_scheduler()` but do NOT modify `run_scheduler()` itself. Task refactor-05 will wire them up.

Reference: `project-management/drafts/10-2026-02-15-scheduler-refactor.md` (Phase 3: Spawn)

## What to do

Add the following functions to `orchestrator/scheduler.py`, placed AFTER the guard functions and `run_housekeeping()` (from refactor-02/03) and BEFORE `run_scheduler()`:

### 1. `_requeue_task(task_id: str) -> None`

Requeue a claimed task back to incoming on spawn failure:

```python
def _requeue_task(task_id: str) -> None:
    """Requeue a claimed task back to incoming after spawn failure."""
    try:
        from .queue_utils import get_sdk
        sdk = get_sdk()
        sdk.tasks.update(task_id, queue="incoming", claimed_by=None)
        debug_log(f"Requeued task {task_id} back to incoming")
    except Exception as e:
        debug_log(f"Failed to requeue task {task_id}: {e}")
```

This replaces the inline try/except at lines 1777-1782.

### 2. `spawn_implementer(ctx: AgentContext) -> int`

Spawn an implementer: prepare task dir, invoke claude directly.

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
```

References: `prepare_task_directory()` (line 721), `invoke_claude()` (line 819), `mark_started()` and `save_state()` from state_utils.

### 3. `spawn_lightweight(ctx: AgentContext) -> int`

Spawn a lightweight agent (no worktree, runs in parent project).

```python
def spawn_lightweight(ctx: AgentContext) -> int:
    """Spawn a lightweight agent (no worktree, runs in parent project)."""
    write_agent_env(ctx.agent_name, ctx.agent_config.get("id", 0), ctx.role, ctx.agent_config)
    pid = spawn_agent(ctx.agent_name, ctx.agent_config.get("id", 0), ctx.role, ctx.agent_config)

    new_state = mark_started(ctx.state, pid)
    save_state(new_state, ctx.state_path)
    return pid
```

References: `write_agent_env()` (line 535), `spawn_agent()` (line 587).

### 4. `spawn_worktree(ctx: AgentContext) -> int`

Spawn an agent with a worktree (general case for non-lightweight, non-implementer).

This is the most complex strategy. It needs to:

1. Resolve the base branch for the worktree:
   - Start with `agent_config.get("base_branch", get_main_branch())`
   - If there's a `claimed_task` with a branch that isn't "main", use that
   - Otherwise, for non-claimable agents, call `peek_task_branch(role)` for a branch hint
2. Call `ensure_worktree(agent_name, base_branch)`
3. If `role == "orchestrator_impl"`, init the submodule (replicate the logic from lines 1818-1857)
4. Call `setup_agent_commands(agent_name, role)`
5. Call `generate_agent_instructions(agent_name, role, agent_config)`
6. Call `write_agent_env(agent_name, agent_config.get("id", 0), role, agent_config)`
7. Call `spawn_agent(agent_name, agent_config.get("id", 0), role, agent_config)`
8. Save state with `mark_started()`

Extract the submodule init logic (lines 1818-1857) into a helper `_init_submodule(agent_name: str) -> None` for clarity:

```python
def _init_submodule(agent_name: str) -> None:
    """Initialize the orchestrator submodule in an agent's worktree."""
    import subprocess
    worktree_path = get_worktree_path(agent_name)
    try:
        subprocess.run(
            ["git", "submodule", "update", "--init", "orchestrator"],
            cwd=worktree_path,
            capture_output=True, text=True, timeout=120,
        )
        sub_path = worktree_path / "orchestrator"
        subprocess.run(["git", "checkout", "main"], cwd=sub_path, capture_output=True, text=True, timeout=30)
        subprocess.run(["git", "fetch", "origin", "main"], cwd=sub_path, capture_output=True, text=True, timeout=60)
        subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=sub_path, capture_output=True, text=True, timeout=30)
        _verify_submodule_isolation(sub_path, agent_name)
        debug_log(f"Submodule initialized for {agent_name}")
    except Exception as e:
        debug_log(f"Submodule init failed for {agent_name}: {e}")
```

Note: `subprocess` is already imported at line 8 of scheduler.py.

References: `ensure_worktree()` from `.git_utils`, `get_worktree_path()` from `.git_utils`, `peek_task_branch()` (line 182), `setup_agent_commands()` (line 318), `generate_agent_instructions()` (line 346), `_verify_submodule_isolation()` (line 132).

### 5. `get_spawn_strategy(ctx: AgentContext) -> Callable`

Select the correct spawn strategy based on agent config:

```python
def get_spawn_strategy(ctx: AgentContext):
    """Select spawn strategy based on agent type."""
    if ctx.role == "implementer" and ctx.claimed_task:
        return spawn_implementer
    if ctx.agent_config.get("lightweight", False):
        return spawn_lightweight
    return spawn_worktree
```

## What NOT to do

- Do NOT modify `run_scheduler()` or any existing functions
- Do NOT change tests
- Do NOT change `prepare_task_directory()`, `invoke_claude()`, or any existing helpers

## Key files

- `orchestrator/scheduler.py` -- add spawn strategies here
- `orchestrator/git_utils.py` -- `ensure_worktree()`, `get_worktree_path()`
- `orchestrator/state_utils.py` -- `mark_started()`, `save_state()`
- `project-management/drafts/10-2026-02-15-scheduler-refactor.md` -- design reference

## Acceptance criteria

- [ ] `spawn_implementer(ctx)` function exists and replicates lines 1770-1792 logic
- [ ] `spawn_lightweight(ctx)` function exists and replicates the lightweight spawn logic
- [ ] `spawn_worktree(ctx)` function exists and replicates the worktree spawn logic including submodule init
- [ ] `_init_submodule(agent_name)` helper extracted from lines 1818-1857
- [ ] `_requeue_task(task_id)` helper exists for error recovery
- [ ] `get_spawn_strategy(ctx)` dispatches correctly: implementer with task -> spawn_implementer, lightweight -> spawn_lightweight, default -> spawn_worktree
- [ ] No changes to `run_scheduler()` or any existing functions
- [ ] All existing tests pass (`pytest tests/`)
