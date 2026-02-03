# /pause-agent - Pause/Resume Agent

Temporarily disable or re-enable an agent.

## Usage

```
/pause-agent <agent-name>
/pause-agent impl-agent-1
```

## What It Does

Sets `paused: true` in the agent's configuration in `.orchestrator/agents.yaml`.

When paused:
- Agent won't be started by scheduler
- Existing work continues if running
- State is preserved

## Example

Before:
```yaml
agents:
  - name: impl-agent-1
    role: implementer
    interval_seconds: 180
```

After `/pause-agent impl-agent-1`:
```yaml
agents:
  - name: impl-agent-1
    role: implementer
    interval_seconds: 180
    paused: true
```

## Resuming an Agent

Run the command again to toggle, or manually edit:
```yaml
    paused: false  # or remove the line
```

## Use Cases

- **Debugging** - Pause agents while investigating issues
- **Maintenance** - Stop processing during updates
- **Resource management** - Reduce load temporarily
- **Manual intervention** - Take over a task manually

## Checking Status

```
/agent-status
```

Paused agents show as:
```
NAME           STATUS
impl-agent-1   paused
```

## Related Commands

- `/agent-status` - Show all agents
- `/add-agent` - Add new agent
