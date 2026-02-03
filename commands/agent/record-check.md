# Record Check Result

Record the result of a gatekeeper check on a PR.

## Usage

After completing your review of the PR, use this skill to record your findings by writing a markdown file.

## Check Statuses

- `passed` - PR passes this check with no issues
- `failed` - PR has issues that must be fixed before merging
- `warning` - PR has minor issues but can proceed with caution

## Recording a Check Result

### Step 1: Get PR number and check name

The PR number is in the `PR_NUMBER` environment variable.
Your focus area (check name) is in the `AGENT_FOCUS` environment variable.

### Step 2: Write the check result file

Write the result to `.orchestrator/shared/prs/PR-{number}/checks/{check_name}.md`:

```bash
# Get values from environment
PR_NUMBER="${PR_NUMBER}"
CHECK_NAME="${AGENT_FOCUS}"

# Ensure the directory exists
mkdir -p ".orchestrator/shared/prs/PR-${PR_NUMBER}/checks"

# Write the check result
cat > ".orchestrator/shared/prs/PR-${PR_NUMBER}/checks/${CHECK_NAME}.md" << 'EOF'
# ✅ Lint Check

**Status:** passed
**Time:** 2024-01-15T10:30:00Z
**Summary:** All lint checks pass, no style issues found.

## Details

Analyzed 15 modified files for lint and style issues.

### Findings
- No ESLint errors
- No TypeScript type issues
- Formatting matches project standards
EOF
```

## Check File Format

```markdown
# {emoji} {Check Name} Check

**Status:** passed | failed | warning
**Time:** {ISO8601_timestamp}
**Summary:** {one-line summary}

## Details

{detailed markdown report}

## Issues

- **{severity}** `{file}:{line}`: {message}
- **{severity}** `{file}:{line}`: {message}
```

### Status Emojis
- `✅` for passed
- `❌` for failed
- `⚠️` for warning

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

```bash
cat > ".orchestrator/shared/prs/PR-${PR_NUMBER}/checks/tests.md" << 'EOF'
# ❌ Tests Check

**Status:** failed
**Time:** 2024-01-15T10:30:00Z
**Summary:** 2 test failures in auth module

## Details

## Test Results

**Total:** 127 tests
**Passed:** 125
**Failed:** 2

### Failures

1. `test_login_invalid_password` - Expected error message changed
2. `test_session_expiry` - Timeout assertion off by 1 second

### Recommendation
The test expectations need to be updated to match the new behavior.

## Issues

- **error** `tests/auth/test_login.py:45`: Expected error message 'Invalid password' but got 'Incorrect password'
- **error** `tests/auth/test_session.py:78`: Session expiry assertion failed: expected 3600, got 3601
EOF
```

## Example: Passed with Notes

```bash
cat > ".orchestrator/shared/prs/PR-${PR_NUMBER}/checks/architecture.md" << 'EOF'
# ✅ Architecture Check

**Status:** passed
**Time:** 2024-01-15T10:30:00Z
**Summary:** Architecture changes are well-structured

## Details

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
EOF
```

## After Recording

The check result will be:
1. Stored in `.orchestrator/shared/prs/PR-{number}/checks/`
2. Aggregated by the gatekeeper coordinator
3. Used to determine if PR passes or needs fixes
