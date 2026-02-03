# Create Proposal

Create a new proposal for the curator to evaluate.

## Proposal Format

Proposals are markdown files with specific metadata:

```markdown
# Proposal: {Title}

**ID:** PROP-{uuid8}
**Proposer:** {your_agent_name}
**Category:** test | refactor | feature | debt | plan-task
**Complexity:** S | M | L | XL
**Created:** {ISO8601_timestamp}

## Summary
One-line description of what this proposes.

## Rationale
Why this matters. What problem does it solve?

## Complexity Reduction
(Optional) How this simplifies or unblocks other work.

## Dependencies
(Optional) What must happen first.

## Enables
(Optional) What this unblocks.

## Acceptance Criteria
- [ ] Specific, measurable criterion
- [ ] Another criterion

## Relevant Files
- path/to/relevant/file.ts
- path/to/another/file.py
```

## Fields

### Category
- `test` - Test quality improvements (coverage, flaky tests, assertions)
- `refactor` - Code structure improvements (simplification, patterns)
- `feature` - New functionality
- `debt` - Technical debt reduction
- `plan-task` - Tasks extracted from project plans

### Complexity
- `S` - Few hours, single file
- `M` - Day or two, few files
- `L` - Several days, multiple components
- `XL` - Week+, architectural changes

## Writing Good Proposals

### Title
- Be specific: "Add retry logic to API client"
- Not vague: "Improve error handling"

### Summary
- One clear sentence
- What will change, not how

### Rationale
- Explain WHY this matters
- Connect to project goals
- Quantify impact if possible

### Acceptance Criteria
- Specific and verifiable
- Each criterion independently testable
- Include edge cases

### Relevant Files
- List files that will be affected
- Helps curator detect conflicts
- Helps implementer scope work

## Example

```markdown
# Proposal: Add retry logic to API client

**ID:** PROP-a1b2c3d4
**Proposer:** architect
**Category:** refactor
**Complexity:** M
**Created:** 2024-01-15T10:30:00Z

## Summary
Add exponential backoff retry logic to all external API calls.

## Rationale
Currently, transient network failures cause immediate errors. Adding retry
logic will improve reliability and reduce failed operations by ~80% based
on error logs showing most failures are transient.

## Complexity Reduction
Will allow us to remove ad-hoc retry logic in 5 different places, replacing
it with a single, well-tested implementation.

## Dependencies
None - this is a standalone improvement.

## Enables
- Unblocks the batch processing feature (needs reliable API calls)
- Reduces on-call burden from transient failures

## Acceptance Criteria
- [ ] All external API calls use retry wrapper
- [ ] Exponential backoff with jitter (100ms base, 5 max retries)
- [ ] Retries are logged for observability
- [ ] Non-retryable errors (4xx) fail immediately
- [ ] Unit tests cover retry behavior

## Relevant Files
- src/api/client.ts
- src/api/retry.ts (new)
- src/services/external-service.ts
```

## Creating the Proposal

After analyzing the codebase, create the proposal:

```python
from orchestrator.orchestrator.proposal_utils import create_proposal

create_proposal(
    title="Add retry logic to API client",
    proposer="architect",
    category="refactor",
    complexity="M",
    summary="Add exponential backoff retry logic to all external API calls.",
    rationale="Currently, transient network failures cause immediate errors...",
    acceptance_criteria=[
        "All external API calls use retry wrapper",
        "Exponential backoff with jitter",
        "Unit tests cover retry behavior",
    ],
    relevant_files=["src/api/client.ts", "src/services/external-service.ts"],
    complexity_reduction="Will allow us to remove ad-hoc retry logic...",
    enables="Unblocks the batch processing feature...",
)
```

## After Creation

The proposal will be:
1. Placed in `.orchestrator/shared/proposals/active/`
2. Evaluated by the curator on their next run
3. Promoted to a task, deferred, or rejected with feedback
