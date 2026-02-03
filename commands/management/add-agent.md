# /add-agent - Add New Agent

Add a new agent to the orchestrator configuration.

## Usage

```
/add-agent
```

## Interactive Mode

I'll ask for:

1. **Name** - Unique identifier (e.g., `impl-agent-2`)
2. **Role** - One of:
   - `product_manager` - Creates tasks
   - `implementer` - Implements features
   - `tester` - Runs/writes tests
   - `reviewer` - Reviews code
3. **Interval** - How often to run (in seconds)

## Configuration File

Agents are configured in `.orchestrator/agents.yaml`:

```yaml
agents:
  - name: pm-agent
    role: product_manager
    interval_seconds: 600  # 10 minutes

  - name: impl-agent-1
    role: implementer
    interval_seconds: 180  # 3 minutes

  - name: impl-agent-2
    role: implementer
    interval_seconds: 180

  - name: test-agent
    role: tester
    interval_seconds: 120  # 2 minutes

  - name: review-agent
    role: reviewer
    interval_seconds: 300  # 5 minutes
```

## Optional Fields

```yaml
- name: impl-agent-1
  role: implementer
  interval_seconds: 180
  paused: false          # Set to true to disable
  base_branch: main      # Default branch for worktree
```

## Port Allocation

Each agent gets unique ports based on its position:

```
Agent ID 0: ports 41000-41009
Agent ID 1: ports 41010-41019
Agent ID 2: ports 41020-41029
...
```

Ports allocated per agent:
- DEV_PORT: base + 0
- MCP_PORT: base + 1
- PW_WS_PORT: base + 2

## After Adding

1. The scheduler will pick up the new agent on next tick
2. A worktree will be created in `.orchestrator/agents/{name}/worktree/`
3. State will be tracked in `.orchestrator/agents/{name}/state.json`

## Related Commands

- `/agent-status` - Show all agents
- `/pause-agent` - Pause an agent
- `/tune-intervals` - Adjust agent intervals
