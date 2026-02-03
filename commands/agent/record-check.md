# Record Check Result

Record the result of a gatekeeper check on a PR.

## Usage

After completing your review of the PR, use this skill to record your findings.

## Check Statuses

- `passed` - PR passes this check with no issues
- `failed` - PR has issues that must be fixed before merging
- `warning` - PR has minor issues but can proceed with caution

## Result Format

```python
from orchestrator.orchestrator.pr_utils import record_check_result

# Get PR number from environment
import os
pr_number = int(os.environ.get("PR_NUMBER"))

record_check_result(
    pr_number=pr_number,
    check_name="lint",  # Your focus area
    status="passed",    # or "failed" or "warning"
    summary="All lint checks pass, no style issues found.",
    details="""
## Detailed Report

Analyzed 15 modified files for lint and style issues.

### Findings
- No ESLint errors
- No TypeScript type issues
- Formatting matches project standards
""",
    issues=[  # Optional: list of specific issues
        {
            "file": "src/api/client.ts",
            "line": 42,
            "message": "Consider extracting magic number to constant",
            "severity": "warning"
        }
    ]
)
```

## Fields

### Required
- `pr_number` - The PR being checked (from PR_NUMBER env var)
- `check_name` - Your focus area (lint, tests, style, architecture, etc.)
- `status` - passed | failed | warning
- `summary` - One-line summary of result

### Optional
- `details` - Full markdown report with findings
- `issues` - List of specific issues found

## Issue Format

Each issue in the `issues` list should have:
- `file` - Path to the file
- `line` - Line number (optional)
- `message` - Description of the issue
- `severity` - error | warning | info

## Guidelines

### For Passed Checks
- Briefly confirm what was verified
- Note any edge cases that were considered

### For Failed Checks
- Be specific about what failed and why
- Provide actionable feedback for fixing
- Reference specific files and lines
- Suggest solutions when possible

### For Warnings
- Explain why it's a warning not a failure
- Indicate if it should block merge or not
- Suggest improvements for future PRs

## Example: Failed Check

```python
record_check_result(
    pr_number=pr_number,
    check_name="tests",
    status="failed",
    summary="2 test failures in auth module",
    details="""
## Test Results

**Total:** 127 tests
**Passed:** 125
**Failed:** 2

### Failures

1. `test_login_invalid_password` - Expected error message changed
2. `test_session_expiry` - Timeout assertion off by 1 second

### Recommendation
The test expectations need to be updated to match the new behavior.
""",
    issues=[
        {
            "file": "tests/auth/test_login.py",
            "line": 45,
            "message": "Expected error message 'Invalid password' but got 'Incorrect password'",
            "severity": "error"
        },
        {
            "file": "tests/auth/test_session.py",
            "line": 78,
            "message": "Session expiry assertion failed: expected 3600, got 3601",
            "severity": "error"
        }
    ]
)
```

## Example: Passed with Notes

```python
record_check_result(
    pr_number=pr_number,
    check_name="architecture",
    status="passed",
    summary="Architecture changes are well-structured",
    details="""
## Architecture Review

### Changes Reviewed
- New service layer for user management
- Updated dependency injection patterns
- Modified API boundaries

### Assessment
The changes follow established patterns and maintain proper separation of concerns.
The new UserService correctly implements the Repository pattern.

### Recommendations (Non-blocking)
- Consider adding interface documentation for the new service
- The service could benefit from caching in future iterations
"""
)
```

## After Recording

The check result will be:
1. Stored in `.orchestrator/shared/prs/PR-{number}/checks/`
2. Aggregated by the gatekeeper coordinator
3. Used to determine if PR passes or needs fixes
