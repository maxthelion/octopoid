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

### Step 1: Defer both proposals

```bash
PROP1="PROP-abc12345"
PROP2="PROP-def67890"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Defer proposal 1
mkdir -p .orchestrator/shared/proposals/deferred
mv ".orchestrator/shared/proposals/active/${PROP1}.md" \
   ".orchestrator/shared/proposals/deferred/${PROP1}.md"
cat >> ".orchestrator/shared/proposals/deferred/${PROP1}.md" << EOF

---
## Deferral

**Deferred:** ${TIMESTAMP}

### Reason

Deferred pending conflict resolution with ${PROP2}.
EOF

# Defer proposal 2
mv ".orchestrator/shared/proposals/active/${PROP2}.md" \
   ".orchestrator/shared/proposals/deferred/${PROP2}.md"
cat >> ".orchestrator/shared/proposals/deferred/${PROP2}.md" << EOF

---
## Deferral

**Deferred:** ${TIMESTAMP}

### Reason

Deferred pending conflict resolution with ${PROP1}.
EOF
```

### Step 2: Create escalation message

```bash
mkdir -p .orchestrator/messages

cat > ".orchestrator/messages/warning-$(date +%Y%m%d-%H%M%S)-conflict.md" << 'EOF'
# ⚠️ Conflict: PROP-abc12345 vs PROP-def67890

**Type:** warning
**Time:** 2024-01-15T14:30:00
**From:** curator

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
EOF
```

## After Escalation

- Both proposals are in `proposals/deferred/`
- A message is in `.orchestrator/messages/`
- The user's Claude session will show the message
- Once resolved, the owner should reactivate the chosen proposal(s)
