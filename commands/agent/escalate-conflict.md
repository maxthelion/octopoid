# Escalate Conflict

Escalate conflicting proposals to the project owner.

## Usage

```
/escalate-conflict PROP-abc12345 PROP-def67890 "Description of conflict"
```

## What Happens

1. A warning message is created for the project owner
2. Both proposals are deferred pending resolution
3. The message includes both proposals and the conflict details

## When to Escalate

Escalate when:

- Two proposals modify the same files in incompatible ways
- Proposals represent different architectural directions
- A refactor conflicts with a feature implementation
- You cannot determine which proposal should take priority
- The decision requires domain knowledge you don't have

## What NOT to Escalate

Don't escalate:
- Simple priority decisions (just promote the higher-priority one)
- Proposals that can be sequenced (do one first, then the other)
- Duplicates (just reject the duplicate)

## Conflict Types

### Same Files
```
Both proposals modify src/api/client.ts:
- PROP-abc: Wants to add retry logic
- PROP-def: Wants to refactor to use a different HTTP library

These cannot proceed simultaneously. One must complete first, or
they need to be combined into a single approach.
```

### Architectural Direction
```
These proposals represent different approaches:
- PROP-abc: Add caching at the API layer
- PROP-def: Add caching at the database layer

Both solve the same problem differently. The project owner should
decide the preferred approach.
```

### Feature vs Refactor
```
- PROP-abc: Add new payment method (feature)
- PROP-def: Refactor payment module for simplicity (refactor)

The refactor would change interfaces the feature depends on.
Need to decide: feature first then refactor, or refactor first?
```

## Implementation

```python
from orchestrator.orchestrator.proposal_utils import defer_proposal
from orchestrator.orchestrator.message_utils import warning

# Defer both proposals
defer_proposal(proposal1['path'], "Deferred pending conflict resolution")
defer_proposal(proposal2['path'], "Deferred pending conflict resolution")

# Create escalation message
body = f"""
Two proposals appear to conflict:

## Proposal 1: {proposal1['title']}
ID: {proposal1['id']}
Category: {proposal1['category']}

## Proposal 2: {proposal2['title']}
ID: {proposal2['id']}
Category: {proposal2['category']}

## Conflict
{conflict_description}

## Options
1. Approve proposal 1, reject proposal 2
2. Approve proposal 2, reject proposal 1
3. Combine into a single approach
4. Sequence them (specify order)

Both proposals have been deferred pending your decision.
"""

warning(
    f"Conflict: {proposal1['id']} vs {proposal2['id']}",
    body,
    agent_name="pm-agent"
)
```

## Message Format

The project owner will see:

```markdown
# ⚠️ Conflict: PROP-abc12345 vs PROP-def67890

**Type:** warning
**Time:** 2024-01-15T14:30:00
**From:** pm-agent

---

Two proposals appear to conflict:

## Proposal 1: Add retry logic to API client
ID: PROP-abc12345
Category: refactor

## Proposal 2: Replace HTTP library
ID: PROP-def67890
Category: refactor

## Conflict
Both proposals modify src/api/client.ts in incompatible ways.

## Options
1. Approve proposal 1, reject proposal 2
2. Approve proposal 2, reject proposal 1
3. Combine into a single approach
4. Sequence them (specify order)

Both proposals have been deferred pending your decision.
```

## After Escalation

- Both proposals are in `proposals/deferred/`
- A message is in `.orchestrator/messages/`
- The user's Claude session will show the message
- Once resolved, the owner should reactivate the chosen proposal(s)
