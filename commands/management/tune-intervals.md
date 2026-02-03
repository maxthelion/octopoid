# /tune-intervals - Adjust Agent Wake Intervals

Configure how often agents wake up and check for work.

## Usage

```
/tune-intervals
```

## What Are Intervals?

Each agent has an `interval_seconds` setting that determines how often the scheduler considers running it.

```yaml
agents:
  - name: impl-agent-1
    role: implementer
    interval_seconds: 180  # Check every 3 minutes
```

## Configuration

Edit `.orchestrator/agents.yaml`:

```yaml
agents:
  - name: pm-agent
    role: product_manager
    interval_seconds: 600    # 10 minutes

  - name: impl-agent-1
    role: implementer
    interval_seconds: 180    # 3 minutes

  - name: test-agent
    role: tester
    interval_seconds: 120    # 2 minutes

  - name: review-agent
    role: reviewer
    interval_seconds: 300    # 5 minutes
```

## How Intervals Work

1. Scheduler runs every minute (via cron)
2. For each agent, it checks:
   - Is the agent paused? → Skip
   - Is the agent already running? → Skip
   - Has `interval_seconds` passed since last start? → Run it
3. If due, spawn the agent

## Interval Guidelines

### Product Manager
```yaml
interval_seconds: 600  # 10 minutes
```
- Creates new tasks
- Doesn't need to run frequently
- Higher interval = fewer tasks created

### Implementer
```yaml
interval_seconds: 180  # 3 minutes
```
- Claims and implements tasks
- Balance responsiveness with resource usage
- Multiple implementers can have same interval

### Tester
```yaml
interval_seconds: 120  # 2 minutes
```
- Runs tests quickly
- Can run more frequently
- Tests are usually fast

### Reviewer
```yaml
interval_seconds: 300  # 5 minutes
```
- Reviews are thoughtful
- Doesn't need to be instant
- Longer interval is fine

## Common Patterns

### High Activity
```yaml
agents:
  - name: impl-agent-1
    interval_seconds: 60   # 1 minute
  - name: impl-agent-2
    interval_seconds: 60
  - name: test-agent
    interval_seconds: 60
```
Multiple agents checking frequently = fast task turnover

### Low Activity
```yaml
agents:
  - name: impl-agent-1
    interval_seconds: 900  # 15 minutes
```
Single agent, infrequent checks = minimal resource usage

### Staggered
```yaml
agents:
  - name: impl-agent-1
    interval_seconds: 180
  - name: impl-agent-2
    interval_seconds: 240  # Different interval
```
Avoids all agents running simultaneously

## Resource Considerations

- Lower intervals = More agent spawns = More API calls
- Each agent run uses Claude API tokens
- Consider cost vs responsiveness

## Checking Status

```
/agent-status
```

Shows when each agent last ran and when next due:
```
NAME           LAST RUN    NEXT DUE
impl-agent-1   2m ago      in 1m
impl-agent-2   5m ago      now
```

## Related Commands

- `/agent-status` - See agent timing
- `/tune-backpressure` - Queue limits
- `/pause-agent` - Stop an agent
