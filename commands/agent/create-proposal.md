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

After analyzing the codebase, create the proposal by writing a markdown file directly.

### Step 1: Generate filename with timestamp

Generate a timestamped filename for the proposal:

```bash
TIMESTAMP=$(date +%Y-%m-%d-%H%M)
FILENAME="${TIMESTAMP}-refactor-proposal.md"
echo $FILENAME
```

Use appropriate suffix based on proposal type:
- `-refactor-proposal.md` for refactoring
- `-implementation-proposal.md` for new features
- `-question.md` for questions needing user input
- `-decision.md` for architectural decisions

### Step 2: Write the proposal file

Write the proposal to `project-management/human-inbox/{FILENAME}`:

```bash
# Ensure the directory exists
mkdir -p project-management/human-inbox

# Write the proposal (replace with your content)
cat > project-management/human-inbox/2024-01-15-1030-refactor-proposal.md << 'EOF'
# Proposal: Add retry logic to API client

**Proposer:** architect
**Category:** refactor
**Complexity:** M
**Created:** 2024-01-15T10:30:00Z

## Summary
Add exponential backoff retry logic to all external API calls.

## Rationale
Currently, transient network failures cause immediate errors...

## Acceptance Criteria
- [ ] All external API calls use retry wrapper
- [ ] Exponential backoff with jitter
- [ ] Unit tests cover retry behavior

## Relevant Files
- src/api/client.ts
- src/services/external-service.ts
EOF
```

### Important Notes

- Use your agent name from the `AGENT_NAME` environment variable as the Proposer
- Use the current timestamp in ISO8601 format for Created
- The file must be placed in `project-management/human-inbox/`
- The filename format is `{YYYY-MM-DD-HHMM}-{type}-proposal.md`

## After Creation

The proposal will be:
1. Placed in `project-management/human-inbox/`
2. Reviewed by the user via their `/human-inbox` command
3. Approved (moved to docs/ and/or tasks created) or rejected with feedback
