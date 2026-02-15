# refactor-11: Update spawn strategies to read from agent directories

ROLE: implement
PRIORITY: P2
BRANCH: feature/client-server-architecture
CREATED: 2026-02-15T00:00:00Z
CREATED_BY: human
SKIP_PR: true
DEPENDS_ON: refactor-05, refactor-07, refactor-08, refactor-10

## Context

After the scheduler refactor (refactor-01 through refactor-06) and the agent directories setup (refactor-07, refactor-08, refactor-10), the spawn strategies still use hardcoded paths for prompts and scripts:

- `prepare_task_directory()` copies scripts from `orchestrator/agent_scripts/` (hardcoded `Path(__file__).parent / "agent_scripts"` at line 752)
- `render_prompt()` uses templates from `orchestrator/prompts/`
- `get_spawn_strategy()` dispatches based on `ctx.role` string matching

This task updates the spawn strategies to read from agent directories instead. The agent directory path comes from `ctx.agent_config["agent_dir"]` (set by the updated `get_agents()` from refactor-10).

Reference:
- `project-management/drafts/10-2026-02-15-scheduler-refactor.md` (Connection to Agent Directories)
- `project-management/drafts/9-2026-02-15-agent-directories.md` (How the Scheduler Uses This)

## What to do

### 1. Update `get_spawn_strategy()` to read spawn_mode from agent config

Instead of hardcoding role names, read `spawn_mode` from the agent config (which comes from `agent.yaml` via the fleet merge):

```python
def get_spawn_strategy(ctx: AgentContext):
    """Select spawn strategy based on agent config."""
    spawn_mode = ctx.agent_config.get("spawn_mode", "worktree")
    is_lightweight = ctx.agent_config.get("lightweight", False)

    if spawn_mode == "scripts" and ctx.claimed_task:
        return spawn_implementer
    if is_lightweight:
        return spawn_lightweight
    return spawn_worktree
```

This means adding a new agent type with `spawn_mode: scripts` automatically gets the implementer spawn path without changing the scheduler.

### 2. Update `prepare_task_directory()` to use agent directory

Currently `prepare_task_directory()` hardcodes the scripts source:
```python
scripts_src = Path(__file__).parent / "agent_scripts"  # line 752
```

Change this to read from the agent directory:

```python
def prepare_task_directory(
    task: dict,
    agent_name: str,
    agent_config: dict,
) -> Path:
    # ... existing setup ...

    # Copy scripts from agent directory (fall back to legacy path)
    agent_dir = agent_config.get("agent_dir")
    if agent_dir:
        scripts_src = Path(agent_dir) / "scripts"
    else:
        scripts_src = Path(__file__).parent / "agent_scripts"  # legacy fallback

    # ... rest of the function ...
```

### 3. Update prompt rendering to use agent directory

Currently `render_prompt()` is called with `role="implementer"` and looks up the prompt template internally. Update `prepare_task_directory()` to read `prompt.md` from the agent directory:

```python
    # Render prompt from agent directory (fall back to legacy)
    agent_dir = agent_config.get("agent_dir")
    if agent_dir and (Path(agent_dir) / "prompt.md").exists():
        prompt_template = (Path(agent_dir) / "prompt.md").read_text()
        # Use the template with render_prompt or direct substitution
    else:
        prompt = render_prompt(role="implementer", ...)  # legacy fallback
```

Check how `render_prompt()` works in `orchestrator/prompt_renderer.py` and determine if it should accept a template path/string, or if the substitution should happen in `prepare_task_directory()` directly.

### 4. Handle instructions.md

If the agent directory has an `instructions.md`, it should be included in the prompt context. Check if `generate_agent_instructions()` can be updated to read from the agent directory, or append instructions.md content to the rendered prompt.

### 5. Preserve fallback to legacy paths

All changes must fall back to the current hardcoded paths when `agent_dir` is not set. This ensures backward compatibility during migration:

```python
if agent_dir and (Path(agent_dir) / "scripts").exists():
    scripts_src = Path(agent_dir) / "scripts"
else:
    scripts_src = Path(__file__).parent / "agent_scripts"
```

### 6. Update spawn_worktree for non-implementer agents

`spawn_worktree()` calls `generate_agent_instructions()` which may also need to read from agent directories. Check if instructions.md from the agent directory should replace or supplement the generated instructions.

## Key files

- `orchestrator/scheduler.py` -- update `get_spawn_strategy()`, `prepare_task_directory()`, spawn strategies
- `orchestrator/prompt_renderer.py` -- check how prompts are rendered, possibly update to accept template path
- `orchestrator/config.py` -- `get_agents()` now provides `agent_dir` in config (from refactor-10)
- `packages/client/agents/implementer/` -- agent directory with scripts/, prompt.md, instructions.md
- `packages/client/agents/gatekeeper/` -- agent directory with scripts/, prompt.md, instructions.md

## Acceptance criteria

- [ ] `get_spawn_strategy()` reads `spawn_mode` from agent config instead of hardcoding role names
- [ ] `prepare_task_directory()` copies scripts from agent directory (with legacy fallback)
- [ ] Prompt rendering uses `prompt.md` from agent directory (with legacy fallback)
- [ ] Instructions from `instructions.md` are included in prompt context
- [ ] All changes fall back to legacy paths when `agent_dir` is not set
- [ ] No hardcoded prompt/script paths in new code paths
- [ ] Adding a new agent type = create a directory with agent.yaml/prompt.md/scripts/, no scheduler changes needed
- [ ] Scheduler runs correctly with `--debug --once`
- [ ] All existing tests pass
