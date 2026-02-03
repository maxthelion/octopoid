# /agent-status - Show Agent State

Display the status of all configured agents.

## Usage

```
/agent-status
```

## What It Shows

### Agent Overview
```
Agent Status
============

NAME           ROLE              STATUS    LAST RUN      NEXT DUE
pm-agent       product_manager   idle      5m ago        in 5m
impl-agent-1   implementer       running   2m ago        -
impl-agent-2   implementer       idle      10m ago       in 2m
test-agent     tester            idle      3m ago        in 1m
review-agent   reviewer          paused    1h ago        -
```

### Status Values
- `idle` - Not running, waiting for next scheduled run
- `running` - Currently executing
- `paused` - Disabled in config (paused: true)
- `failed` - Last run failed (shows consecutive failures)

### Detailed View

For each agent:
```
impl-agent-1
------------
Role:          implementer
Status:        running
PID:           12345
Interval:      180s (3m)
Last Started:  2024-01-15T14:30:00
Current Task:  TASK-abc123
Total Runs:    42
Successes:     40
Failures:      2
Consecutive Failures: 0
```

## Implementation

To get agent status programmatically:

```python
from pathlib import Path
from orchestrator.orchestrator.config import get_agents, get_agents_runtime_dir
from orchestrator.orchestrator.state_utils import load_state

for agent in get_agents():
    name = agent['name']
    state_path = get_agents_runtime_dir() / name / 'state.json'
    state = load_state(state_path)

    print(f"{name}: {'running' if state.running else 'idle'}")
    print(f"  Last run: {state.last_started}")
    print(f"  Total runs: {state.total_runs}")
```

## Related Commands

- `/queue-status` - Show task queue
- `/pause-agent` - Pause an agent
- `/add-agent` - Add new agent
