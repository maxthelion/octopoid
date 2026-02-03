# Reject Proposal

Reject a proposal with constructive feedback.

## Usage

```
/reject-proposal PROP-abc12345 "Reason for rejection"
```

## What Happens

1. Proposal is moved from `proposals/active/` to `proposals/rejected/`
2. Rejection reason is appended to the proposal file
3. The proposer will see this feedback on their next run

## When to Reject

Reject a proposal when:

- It's fundamentally flawed in approach
- It's out of scope for the project
- It duplicates existing or in-progress work
- It conflicts with project direction
- It's too vague to be actionable (after consideration)

## Rejection vs Deferral

**Reject** when:
- The idea itself is problematic
- It should NOT be done (now or later)
- The proposer needs to rethink their approach

**Defer** when:
- It's a good idea but wrong timing
- It's blocked by dependencies
- The queue is full (backpressure)

## Writing Good Feedback

The proposer will use your feedback to improve. Be:

### Specific
❌ "This is too vague"
✅ "The acceptance criteria don't specify what error handling is needed"

### Constructive
❌ "This won't work"
✅ "This approach won't work because X. Consider Y instead."

### Actionable
❌ "Not aligned with priorities"
✅ "We're currently focused on stability. Resubmit after the v2.0 release."

### Encouraging when appropriate
✅ "Good observation about the flaky tests. Consider splitting this into separate proposals for each test file."

## Example Rejections

### Too Broad
```
This proposal covers too much ground. "Improve error handling across the app"
could be a month of work. Consider:
1. Identify the top 3 error-prone areas from logs
2. Create separate proposals for each
3. Start with the highest-impact area
```

### Wrong Approach
```
Adding retry logic at the HTTP layer will cause issues with non-idempotent
operations. Instead, consider:
1. Adding retries only for GET requests
2. Using a circuit breaker pattern for other methods
3. Making the retry policy configurable per endpoint
```

### Duplicate Work
```
This is already being addressed in PR #142 which adds the same functionality.
Check open PRs before proposing to avoid duplicates.
```

### Wrong Timing
```
This is a good observation, but we're in a feature freeze for the next release.
Please resubmit after 2024-02-01 when we start the next planning cycle.
```

## Implementation

```python
from orchestrator.orchestrator.proposal_utils import reject_proposal

reject_proposal(
    proposal_path=proposal['path'],
    rejected_by="pm-agent",
    reason="""
    This proposal is too broad. "Comprehensive logging" could mean many things.

    Consider:
    1. Split into smaller, focused proposals
    2. Start with error logging specifically
    3. Specify the logging framework to use
    """
)
```

## After Rejection

- The proposal moves to `proposals/rejected/`
- The proposer sees the feedback on their next run
- They may resubmit an improved version
