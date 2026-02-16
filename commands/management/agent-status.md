# /agent-status - Show Agent State

Display the status of all configured agents.

## Usage

```
/agent-status
```

## What It Shows

### Blueprint Pool Status
```
Agent Blueprints
================

BLUEPRINT         ROLE              MAX   RUNNING   IDLE    STATUS
implementer       implementer       3     1         2       active
sanity-check-gk   custom            1     0         1       active
github-monitor    custom            1     0         1       paused
```

### Running Instances
```
INSTANCE              BLUEPRINT         STATUS    TASK              UPTIME
implementer-1         implementer       running   TASK-abc123       2m
implementer-2         implementer       idle      -                 -
implementer-3         implementer       idle      -                 -
sanity-check-gk-1     sanity-check-gk   idle      -                 -
```

### Status Values
- `idle` - Not running, waiting for next scheduled run
- `running` - Currently executing
- `paused` - Disabled in config (paused: true)
- `failed` - Last run failed (shows consecutive failures)

### Detailed View

For each blueprint:
```
implementer (Blueprint)
-----------------------
Role:           implementer
Max Instances:  3
Running:        1 / 3
Interval:       60s (1m)
Status:         active

Instances:
  implementer-1: running (TASK-abc123, PID 12345)
  implementer-2: idle (last run: 5m ago)
  implementer-3: idle (never run)
```

## Implementation

To get agent status programmatically:

```python
from pathlib import Path
from orchestrator.orchestrator.config import get_agents, get_agents_runtime_dir
from orchestrator.orchestrator.state_utils import load_state, is_process_running

# Get blueprints
blueprints = get_agents()

for blueprint_name, blueprint_config in blueprints.items():
    role = blueprint_config['role']
    max_instances = blueprint_config.get('max_instances', 1)

    print(f"{blueprint_name}: {role}, max_instances={max_instances}")

    # Count running instances
    runtime_dir = get_agents_runtime_dir()
    for agent_dir in runtime_dir.iterdir():
        if agent_dir.name.startswith(f"{blueprint_name}-"):
            state_path = agent_dir / 'state.json'
            state = load_state(state_path)
            status = 'running' if state.running and is_process_running(state.pid) else 'idle'
            print(f"  {agent_dir.name}: {status}")
```

## Related Commands

- `/queue-status` - Show task queue
- `/pause-agent` - Pause an agent
- `/add-agent` - Add new agent
