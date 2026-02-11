# Octopoid Configuration Directory

This directory contains all configuration and runtime state for Octopoid v2.0.

## Files

### `config.yaml` (COMMIT THIS)
Main configuration file - **single source of truth** for all components:
- **Dashboard** reads server URL from here
- **Scheduler** reads server URL from here
- **Agents** read server URL from here
- **Scripts** read server URL from here

All components automatically detect and use this config.

### `agents.yaml` (COMMIT THIS)
Agent definitions:
- What agents to run (implementer, github-issue-monitor, etc.)
- How often they run (interval_seconds)
- What models they use
- Which agents are enabled/disabled

### `runtime/` (DO NOT COMMIT)
Runtime state:
- PIDs of running processes
- Lock files
- Orchestrator registration ID
- Agent state

### `logs/` (DO NOT COMMIT)
Log files:
- Scheduler logs
- Agent logs
- Task-specific logs

### `worktrees/` (DO NOT COMMIT)
Git worktrees for task isolation

## Setup

Initialize Octopoid in your project:

```bash
# This creates .octopoid/ with default config
octopoid init --server http://localhost:8787
```

Or manually create `config.yaml`:

```yaml
server:
  enabled: true
  url: http://localhost:8787  # Your Octopoid API server
  cluster: dev                 # Cluster name
  machine_id: local-dev        # Machine identifier

repo:
  path: /path/to/your/project
  main_branch: main

agents:
  max_concurrent: 3
```

## Usage

All components automatically use this config:

```bash
# Dashboard auto-detects config
python octopoid-dash.py

# Scheduler auto-detects config
python -m orchestrator.scheduler

# All scripts read from .octopoid/config.yaml
```

## Migration from v1.x

If you have `.orchestrator/` (v1.x), migrate to `.octopoid/` (v2.0):

1. Create `.octopoid/config.yaml` with server settings
2. Create `.octopoid/agents.yaml` from `.orchestrator/agents.yaml`
3. Point all components to API server (no local database)
