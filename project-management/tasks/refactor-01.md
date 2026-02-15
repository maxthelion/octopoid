# refactor-01: Extract AgentContext dataclass from scheduler

ROLE: implement
PRIORITY: P1
BRANCH: feature/client-server-architecture
CREATED: 2026-02-15T00:00:00Z
CREATED_BY: human
SKIP_PR: true

## Context

The scheduler (`orchestrator/scheduler.py`) is ~1974 lines with a monolithic `run_scheduler()` function. The agent for-loop (starting at line 1677) passes per-agent state through local variables (`agent_name`, `role`, `interval`, `state`, `state_path`, `claimed_task`, etc.). This scattered state makes it impossible to extract the loop body into composable functions.

This task is the first step of the scheduler refactor pipeline (see `project-management/drafts/10-2026-02-15-scheduler-refactor.md`, Phase 2). We create the `AgentContext` dataclass that will hold all per-agent state, but we do NOT change `run_scheduler()` yet. Later tasks will wire it up.

## What to do

Add an `AgentContext` dataclass to `orchestrator/scheduler.py`, placed after the imports and before any function definitions (near the top of the file, after the `DEBUG` / `_log_file` globals around line 53).

### Dataclass definition

```python
from dataclasses import dataclass, field

@dataclass
class AgentContext:
    """Everything the filter chain needs to evaluate and spawn an agent."""
    agent_config: dict
    agent_name: str
    role: str
    interval: int
    state: AgentState
    state_path: Path
    claimed_task: dict | None = None
```

### Important details

- `AgentState` is already imported from `.state_utils` (line 42-49 of scheduler.py)
- `Path` is already imported from `pathlib` (line 11)
- `dataclass` needs to be imported from `dataclasses` -- check if it's already imported, if not add it
- The `field` import from dataclasses is NOT needed for this dataclass (no `field(default_factory=...)` usage), but you may include it for consistency
- Place the dataclass AFTER all imports and the `DEBUG`/`_log_file` globals, BEFORE `setup_scheduler_debug()`

### What NOT to do

- Do NOT modify `run_scheduler()` or any other existing function
- Do NOT add any new functions beyond the dataclass
- Do NOT refactor any existing code
- Do NOT change tests

## Key files

- `orchestrator/scheduler.py` -- add the dataclass here (after imports, before functions)
- `orchestrator/state_utils.py` -- contains `AgentState` (already imported by scheduler.py)
- `project-management/drafts/10-2026-02-15-scheduler-refactor.md` -- design reference

## Acceptance criteria

- [ ] `AgentContext` dataclass exists in `orchestrator/scheduler.py`
- [ ] Has all 7 fields: `agent_config` (dict), `agent_name` (str), `role` (str), `interval` (int), `state` (AgentState), `state_path` (Path), `claimed_task` (dict | None, default None)
- [ ] Placed after imports and globals, before function definitions
- [ ] No changes to `run_scheduler()` or any existing functions
- [ ] All existing tests pass (`pytest tests/`)
- [ ] File still parses correctly (`python -c "from orchestrator.scheduler import AgentContext"`)
