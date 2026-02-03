# Promote Proposal

Move a proposal from the active queue to the task queue.

## Usage

```
/promote-proposal PROP-abc12345
```

## What Happens

1. Proposal is moved from `proposals/active/` to `proposals/promoted/`
2. A corresponding task is created in `queue/incoming/`
3. The proposal is marked with promotion timestamp

## When to Promote

Promote a proposal when:

- It aligns with current project priorities
- It is well-scoped and actionable
- All dependencies are met
- There are no unresolved conflicts with other proposals
- The task queue has capacity (backpressure check)

## Promotion Criteria

Before promoting, verify:

### Priority Alignment
- Does this support current project goals?
- Is now the right time for this work?

### Scope
- Is the proposal specific enough to implement?
- Are acceptance criteria clear and testable?

### Dependencies
- Are prerequisite tasks complete?
- Are required resources available?

### Conflicts
- Does this conflict with other active proposals?
- Does it overlap with work in progress?

## Implementation

```python
from orchestrator.orchestrator.proposal_utils import promote_proposal
from orchestrator.orchestrator.queue_utils import create_task

# First, create the task
task_path = create_task(
    title=proposal['title'],
    role="implement",  # or "test", "review"
    context=proposal['rationale'],
    acceptance_criteria=proposal['acceptance_criteria'],
    priority="P1",
    branch="main",
    created_by="pm-agent",
)

# Extract task ID from path
task_id = task_path.stem  # e.g., "TASK-abc12345"

# Then promote the proposal
promote_proposal(proposal['path'], task_id=task_id)
```

## Task Mapping

When creating the task from a proposal:

| Proposal Field | Task Field |
|----------------|------------|
| title | title |
| rationale | context |
| acceptance_criteria | acceptance_criteria |
| category → implement/test/review | role |
| complexity → P0/P1/P2 | priority |

### Category to Role Mapping
- `test` → role: test
- `refactor`, `feature`, `debt` → role: implement
- `plan-task` → depends on content

### Complexity to Priority Mapping
- `S`, `M` with high value → P1
- `L`, `XL` or lower value → P2
- Urgent/blocking issues → P0

## After Promotion

- The proposal file moves to `proposals/promoted/`
- The task appears in `queue/incoming/`
- An implementer will claim and work on it
