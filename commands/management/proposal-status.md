# /proposal-status - View Proposal Queue

Display the current state of the proposal queue.

## Usage

```
/proposal-status
```

## What It Shows

### Queue Counts
```
Proposal Status
===============
Active:    8 proposals
Promoted:  12 proposals (converted to tasks)
Deferred:  3 proposals
Rejected:  5 proposals
```

### Active Proposals
```
Active Proposals
----------------
PROP-abc123 | architect    | refactor | M | Add retry logic
PROP-def456 | test-checker | test     | S | Fix flaky auth tests
PROP-ghi789 | app-designer | feature  | L | User dashboard
```

### Deferred Proposals
```
Deferred Proposals
------------------
PROP-jkl012 | Blocked by TASK-xyz | 2 days ago
PROP-mno345 | Queue backpressure  | 5 days ago
```

### Recent Rejections
```
Recent Rejections
-----------------
PROP-pqr678 | Too broad           | 1 day ago
PROP-stu901 | Wrong timing        | 3 days ago
```

## Voice Weights

Shows configured proposer trust levels:

```
Voice Weights
-------------
plan-reader:  1.5 (high trust)
architect:    1.2
test-checker: 1.0
app-designer: 0.8
```

## Backpressure Status

```
Backpressure
------------
Proposer        | Active | Limit | Status
----------------|--------|-------|--------
test-checker    | 3      | 5     | OK
architect       | 5      | 3     | BLOCKED
app-designer    | 1      | 5     | OK
```

## Implementation

To get proposal status programmatically:

```python
from orchestrator.orchestrator.proposal_utils import get_proposal_status

status = get_proposal_status()

print(f"Active: {status['active']['count']}")
print(f"Deferred: {status['deferred']['count']}")
print(f"Rejected: {status['rejected']['count']}")

for proposal in status['active']['proposals']:
    print(f"  {proposal['id']}: {proposal['title']}")
```

## Related Commands

- `/queue-status` - View task queue
- `/agent-status` - View agent states
- `/tune-backpressure` - Adjust limits
