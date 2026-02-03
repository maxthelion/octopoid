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

### Step 1: Read the proposal and generate task ID

```bash
PROP_ID="PROP-abc12345"
TASK_ID="TASK-$(openssl rand -hex 4)"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
```

### Step 2: Create the task file

```bash
mkdir -p .orchestrator/shared/queue/incoming

cat > ".orchestrator/shared/queue/incoming/${TASK_ID}.md" << 'EOF'
# [TASK-abc12345] Add retry logic to API client

ROLE: implement
PRIORITY: P1
BRANCH: main
CREATED: 2024-01-15T10:30:00Z
CREATED_BY: curator
FROM_PROPOSAL: PROP-abc12345

## Context

Add exponential backoff retry logic to all external API calls.

Currently, transient network failures cause immediate errors. Adding retry
logic will improve reliability.

## Acceptance Criteria
- [ ] All external API calls use retry wrapper
- [ ] Exponential backoff with jitter
- [ ] Unit tests cover retry behavior

## Relevant Files
- src/api/client.ts
- src/services/external-service.ts
EOF
```

### Step 3: Move proposal to promoted

```bash
mkdir -p .orchestrator/shared/proposals/promoted
mv ".orchestrator/shared/proposals/active/${PROP_ID}.md" \
   ".orchestrator/shared/proposals/promoted/${PROP_ID}.md"

# Append promotion info
cat >> ".orchestrator/shared/proposals/promoted/${PROP_ID}.md" << EOF

---
**Promoted:** ${TIMESTAMP}
**Task:** ${TASK_ID}
EOF
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
