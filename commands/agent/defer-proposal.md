# Defer Proposal

Defer a proposal for later consideration.

## Usage

```
/defer-proposal PROP-abc12345 "Reason for deferral"
```

## What Happens

1. Proposal is moved from `proposals/active/` to `proposals/deferred/`
2. Deferral reason is appended to the proposal file
3. The proposal can be reactivated later

## When to Defer

Defer a proposal when:

- It's a good idea but the timing isn't right
- It's blocked by dependencies that aren't complete
- The task queue is full (backpressure)
- It's part of a conflict being escalated
- The project is in a phase where this doesn't fit

## Deferral vs Rejection

**Defer** when:
- The idea is valid
- It should be done eventually
- Circumstances will change to make it appropriate

**Reject** when:
- The idea itself is problematic
- The approach is wrong
- It shouldn't be done at all

## Common Deferral Reasons

### Backpressure
```
The task queue is currently at capacity (15/20 tasks pending).
Deferring until capacity frees up. This is a good proposal and
will be reconsidered soon.
```

### Dependencies
```
This depends on the authentication refactor (TASK-xyz) which is
still in progress. Deferring until that work is complete.
```

### Project Phase
```
We're currently in stabilization mode before the v2.0 release.
Deferring new features until after the release (expected 2024-02-15).
```

### Conflict Escalation
```
This proposal conflicts with PROP-def456. Both have been deferred
and escalated to the project owner for a decision.
```

### Resource Constraints
```
All implementers are currently assigned to high-priority work.
Deferring until an implementer becomes available.
```

## Implementation

```bash
PROP_ID="PROP-abc12345"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Move to deferred directory
mkdir -p .orchestrator/shared/proposals/deferred
mv ".orchestrator/shared/proposals/active/${PROP_ID}.md" \
   ".orchestrator/shared/proposals/deferred/${PROP_ID}.md"

# Append deferral info
cat >> ".orchestrator/shared/proposals/deferred/${PROP_ID}.md" << EOF

---
## Deferral

**Deferred:** ${TIMESTAMP}

### Reason

Task queue at capacity. Will reconsider when queue clears.
EOF
```

## After Deferral

- The proposal moves to `proposals/deferred/`
- It remains there until manually reactivated
- Periodic review of deferred proposals is recommended

## Reactivating Deferred Proposals

To move a deferred proposal back to active:

```bash
PROP_ID="PROP-abc12345"

mv ".orchestrator/shared/proposals/deferred/${PROP_ID}.md" \
   ".orchestrator/shared/proposals/active/${PROP_ID}.md"
```

This moves it back to `proposals/active/` for re-evaluation.
