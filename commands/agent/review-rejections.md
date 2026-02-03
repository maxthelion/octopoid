# Review Rejections

Review your previously rejected proposals with feedback.

## Purpose

Before creating new proposals, you should review why previous proposals
were rejected. This helps you:

1. Avoid repeating the same mistakes
2. Understand project priorities better
3. Refine your proposals to be more likely to succeed

## What You'll See

Rejected proposals include feedback from the curator:

```markdown
# Proposal: Add comprehensive logging

**ID:** PROP-abc12345
**Proposer:** architect
**Category:** refactor
**Complexity:** L
**Created:** 2024-01-10T10:30:00Z

...original proposal content...

---
## Rejection

**Rejected:** 2024-01-11T14:20:00Z
**Rejected By:** curator

### Reason

This proposal is too broad. "Comprehensive logging" could mean many things.
Consider:
1. Split into smaller, focused proposals (error logging, audit logging, etc.)
2. Start with one specific area that has the highest impact
3. Specify what logging framework to use

Also, we're currently focused on stability - new features should wait.
```

## Using the Feedback

### Common Rejection Reasons

**Too Broad**
- Split into smaller proposals
- Focus on one specific area

**Wrong Timing**
- Wait for the right project phase
- Check if dependencies are met

**Duplicates Existing Work**
- Check the task queue first
- Look at open PRs

**Poorly Defined**
- Add specific acceptance criteria
- Include relevant files
- Explain rationale more clearly

**Conflicts with Priorities**
- Align with current project goals
- Check with project owner first

### Before Re-proposing

If you want to re-submit a similar idea:

1. **Address all feedback points** - Don't ignore the curator's comments
2. **Check if the issue still exists** - Maybe it was fixed another way
3. **Verify timing is better** - Has the project phase changed?
4. **Make it more specific** - Tighter scope is usually better

## Implementation

To review your rejected proposals, list the files in the rejected directory:

```bash
# List rejected proposals for your proposer type
PROPOSER="${AGENT_NAME}"
ls -la .orchestrator/shared/proposals/rejected/ 2>/dev/null | grep "${PROPOSER}" || echo "No rejections found"

# Or list all rejected proposals
ls -la .orchestrator/shared/proposals/rejected/

# Read a specific rejection
cat ".orchestrator/shared/proposals/rejected/PROP-abc12345.md"
```

To find rejections mentioning your proposer name:

```bash
grep -l "Proposer.*${AGENT_NAME}" .orchestrator/shared/proposals/rejected/*.md 2>/dev/null
```

## Best Practices

1. **Review rejections at the start** of every proposer run
2. **Learn from patterns** - If multiple proposals fail for similar reasons, adjust your approach
3. **Don't spam** - Quality over quantity
4. **Acknowledge feedback** - If re-proposing, note that you addressed previous feedback
