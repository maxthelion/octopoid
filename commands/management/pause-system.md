# /pause-system - Pause/Resume Entire System

Pause or resume the entire orchestrator system with a single command.

## Usage

```
/pause-system
```

## What It Does

Toggles the top-level `paused` flag in `.orchestrator/agents.yaml`.

When paused:
- Scheduler exits immediately without evaluating any agents
- No new agents will be spawned
- Currently running agents continue until they finish
- All state is preserved

## Example

Before:
```yaml
model: proposal

agents:
  - name: impl-agent-1
    role: implementer
    interval_seconds: 180
```

After `/pause-system`:
```yaml
model: proposal
paused: true

agents:
  - name: impl-agent-1
    role: implementer
    interval_seconds: 180
```

## Resuming the System

Run the command again to toggle, or manually edit:
```yaml
paused: false  # or remove the line
```

## Difference from /pause-agent

- `/pause-agent <name>` - Pauses a single agent
- `/pause-system` - Pauses the entire system (all agents)

Use `/pause-system` when you want to completely stop the orchestrator, for example during maintenance or debugging.

## Checking Status

```
/agent-status
```

When the system is paused, status will show:
```
SYSTEM STATUS: PAUSED

No agents will be spawned until system is resumed.
```

## Related Commands

- `/pause-agent` - Pause individual agent
- `/agent-status` - Show all agents and system status
