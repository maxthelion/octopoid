# /tune-backpressure - Adjust Queue Limits

Configure backpressure settings to control task flow.

## Usage

```
/tune-backpressure
```

## What Is Backpressure?

Backpressure prevents the system from being overwhelmed by:
- Limiting how many tasks can queue up
- Limiting concurrent work
- Limiting open pull requests

## Configuration

Settings in `.orchestrator/agents.yaml`:

```yaml
queue_limits:
  max_incoming: 20    # Max tasks in incoming queue
  max_claimed: 5      # Max tasks being worked on
  max_open_prs: 10    # Max open pull requests

agents:
  - name: impl-agent-1
    ...
```

## Settings Explained

### max_incoming
Maximum tasks allowed in incoming + claimed queues combined.

- **Higher** = More tasks can queue up, risk of stale tasks
- **Lower** = Fewer tasks queue, forces prioritization

Default: 20

### max_claimed
Maximum tasks that can be claimed at once.

- **Higher** = More parallel work, more resource usage
- **Lower** = Less parallel work, more predictable

Default: 5

### max_open_prs
Maximum open pull requests before agents stop creating new ones.

- **Higher** = More PRs awaiting review
- **Lower** = Forces review before new work

Default: 10

## How Backpressure Works

1. **Product Manager** checks `can_create_task()`:
   - If incoming + claimed >= max_incoming: STOP creating tasks

2. **Implementer/Tester/Reviewer** checks `can_claim_task()`:
   - If no tasks in incoming: STOP
   - If claimed >= max_claimed: STOP
   - If open_prs >= max_open_prs: STOP

## Tuning Guidelines

### High Throughput Setup
```yaml
queue_limits:
  max_incoming: 50
  max_claimed: 10
  max_open_prs: 20
```
Good for: Large teams, fast review cycles

### Conservative Setup
```yaml
queue_limits:
  max_incoming: 10
  max_claimed: 2
  max_open_prs: 5
```
Good for: Small teams, careful review process

### Balanced (Default)
```yaml
queue_limits:
  max_incoming: 20
  max_claimed: 5
  max_open_prs: 10
```
Good for: Most projects

## Monitoring

Check current state vs limits:
```
/queue-status
```

```
Queue Status
============
Incoming:  12/20 (60%)
Claimed:   4/5 (80%)
Open PRs:  7/10 (70%)
```

## Related Commands

- `/queue-status` - Current queue state
- `/tune-intervals` - Agent timing
