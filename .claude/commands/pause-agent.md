# /pause-agent - Pause/Resume Agent

Temporarily disable or re-enable an agent.

## Usage

```
/pause-agent <agent-name>
/pause-agent impl-agent-1
```

## What It Does

Sets `paused: true` in the agent's configuration in `.octopoid/agents.yaml`.

When paused:
- Agent won't be started by scheduler
- Existing work continues if running
- State is preserved

## Examples

### Pause an Implementer

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

### Pause a Proposer

```yaml
agents:
  - name: test-checker
    role: proposer
    focus: test_quality
    interval_seconds: 86400
    paused: true  # Won't create new proposals
```

### Pause a Gatekeeper

```yaml
agents:
  - name: lint-checker
    role: gatekeeper
    focus: lint
    interval_seconds: 600
    paused: true  # Won't check PRs
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
- **Proposer control** - Temporarily stop a proposer from creating new proposals
- **Gatekeeper control** - Pause PR checks (e.g., during major refactoring)

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
