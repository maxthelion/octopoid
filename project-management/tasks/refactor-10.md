# refactor-10: Simplify fleet config format in agents.yaml

ROLE: implement
PRIORITY: P2
BRANCH: feature/client-server-architecture
CREATED: 2026-02-15T00:00:00Z
CREATED_BY: human
SKIP_PR: true
DEPENDS_ON: refactor-07, refactor-08

## Context

The current `.octopoid/agents.yaml` format puts all agent config inline:

```yaml
agents:
  - id: 1
    name: implementer-1
    role: implementer
    enabled: true
    interval_seconds: 60
    max_concurrent: 1
    max_turns: 200
    model: sonnet
```

With agent directories (refactor-07, refactor-08), the type defaults live in `agent.yaml` inside each agent directory. The fleet config only needs to specify which agents to run, their names, and any overrides.

This task updates the config format and the `get_agents()` loading code to use the new fleet format while maintaining the same output shape so downstream consumers don't break.

Reference: `project-management/drafts/9-2026-02-15-agent-directories.md` (Instance Configuration section)

## What to do

### 1. Define the new agents.yaml format

```yaml
paused: false

queue_limits:
  max_claimed: 3
  max_incoming: 20
  max_open_prs: 10

fleet:
  - name: implementer-1
    type: implementer
    enabled: true

  - name: implementer-2
    type: implementer
    enabled: true
    model: opus              # override the type default

  - name: github-issue-monitor
    type: custom
    path: .octopoid/agents/github-issue-monitor/
    enabled: true
    interval_seconds: 900
    lightweight: true
```

Key differences from current format:
- `agents:` becomes `fleet:`
- Each entry has `type:` referencing an agent directory instead of inline `role:`, `model:`, etc.
- Fleet entries can override type defaults
- Custom agents use `type: custom` with an explicit `path:`

### 2. Update `get_agents()` in `orchestrator/config.py`

Currently `get_agents()` (line 371) simply reads `config["agents"]`. Update it to:

1. Read the raw config from agents.yaml
2. Check for `fleet:` key (new format) or `agents:` key (old format, for backward compat)
3. For each fleet entry:
   a. Resolve the agent directory path:
      - If `type: custom` and `path:` is set, use that path
      - Otherwise, look in `.octopoid/agents/<type>/agent.yaml`
   b. Load `agent.yaml` from the agent directory (type defaults)
   c. Merge: type defaults (from agent.yaml) < fleet overrides (from agents.yaml)
   d. Ensure `role` is set from the type defaults (if not overridden)
   e. Add `agent_dir` key to the merged config pointing to the agent directory path
4. Return the list of merged configs (same shape as current output)

```python
def get_agents() -> list[dict[str, Any]]:
    """Get list of configured agents.

    Supports two formats:
    - Legacy: agents.yaml with 'agents:' key containing inline config
    - New: agents.yaml with 'fleet:' key referencing agent directories
    """
    config = load_agents_config()

    # Legacy format
    if "agents" in config:
        return config["agents"]

    # New fleet format
    fleet = config.get("fleet", [])
    if not fleet:
        return []

    agents = []
    for entry in fleet:
        agent_type = entry.get("type", "")

        # Resolve agent directory
        if agent_type == "custom":
            agent_dir = Path(entry.get("path", ""))
            if not agent_dir.is_absolute():
                agent_dir = find_parent_project() / agent_dir
        else:
            agent_dir = find_parent_project() / ".octopoid" / "agents" / agent_type

        # Load type defaults from agent.yaml
        type_defaults = {}
        agent_yaml = agent_dir / "agent.yaml"
        if agent_yaml.exists():
            with open(agent_yaml) as f:
                type_defaults = yaml.safe_load(f) or {}

        # Merge: type defaults < fleet overrides
        merged = {**type_defaults, **entry}
        merged["agent_dir"] = str(agent_dir)

        # Ensure 'enabled' defaults to True
        merged.setdefault("enabled", True)

        # Skip disabled agents
        if not merged.get("enabled", True):
            continue

        agents.append(merged)

    return agents
```

### 3. Handle backward compatibility

The `get_agents()` function should support both formats:
- If `agents:` key exists, use the old format (return as-is)
- If `fleet:` key exists, use the new format (load and merge)
- If neither exists, return empty list

This allows a gradual migration. The old format keeps working until projects update.

### 4. Update other config consumers

Check that these functions still work correctly with the new format:
- `get_queue_limits()` -- reads `queue_limits:` (unchanged between formats)
- `is_system_paused()` -- reads `paused:` (unchanged between formats)
- `get_proposers()` -- filters agents by role "proposer"
- `get_curators()` -- filters agents by role "curator"
- `get_gatekeepers()` -- filters agents with gatekeeper role

The merged config should have the same shape (name, role, model, etc.), so these should work without changes. Verify.

### 5. Update the agents.yaml template

Update `packages/client/templates/agents.yaml` (if it exists) to use the new fleet format. This is what gets scaffolded by `octopoid init`.

## Key files

- `orchestrator/config.py` -- update `get_agents()` (line 371)
- `.octopoid/agents.yaml` -- our instance config (will be migrated in refactor-12)
- `packages/client/templates/agents.yaml` -- template for new projects
- `project-management/drafts/9-2026-02-15-agent-directories.md` -- design reference

## Acceptance criteria

- [ ] New `fleet:` format in agents.yaml works correctly
- [ ] `get_agents()` loads fleet entries and merges with agent directory defaults
- [ ] Each merged config has `agent_dir` key pointing to the agent directory
- [ ] Legacy `agents:` format still works (backward compatible)
- [ ] `get_queue_limits()`, `is_system_paused()`, and other config consumers still work
- [ ] Downstream code (scheduler, backpressure, etc.) sees the same config shape
- [ ] All existing tests pass
