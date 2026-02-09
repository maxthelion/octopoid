# Record Check Result

Record the result of a gatekeeper check on a task's branch diff.

## Usage

After completing your review, use this skill to record your findings.

## Check Statuses

- `pass` - Task passes this check with no issues
- `fail` - Task has issues that must be fixed

## Recording a Check Result

### Step 1: Get task ID and check name

The task ID is in the `REVIEW_TASK_ID` environment variable.
Your check name is in the `REVIEW_CHECK_NAME` or `AGENT_FOCUS` environment variable.

### Step 2: Write the check result

Use the DB module to record your result with metadata for debugging:

```python
import os
import sys
from pathlib import Path

# Add orchestrator to path
project_root = Path(os.environ.get("PARENT_PROJECT", "."))
sys.path.insert(0, str(project_root / "orchestrator"))

from orchestrator.db import record_check_result

task_id = os.environ.get("REVIEW_TASK_ID")
check_name = os.environ.get("REVIEW_CHECK_NAME", os.environ.get("AGENT_FOCUS"))
agent_name = os.environ.get("AGENT_NAME")
agent_focus = os.environ.get("AGENT_FOCUS")

# Track tools used during the review (helps debug if wrong tools were available)
tools_used = []  # e.g., ["Read", "Grep", "mcp__playwright__*"]

record_check_result(
    task_id=task_id,
    check_name=check_name,
    status="pass",  # or "fail"
    summary="One-line summary of your verdict and detailed findings",
    metadata={
        "check_performed": check_name,  # What you thought you were checking
        "check_requested": os.environ.get("REVIEW_CHECK_NAME"),  # What dispatcher requested
        "agent_name": agent_name,
        "agent_focus": agent_focus,
        "tools_used": tools_used,  # List of tools you actually used
    },
)
```

**Why metadata matters:**
- `check_performed` vs `check_requested` detects dispatcher mismatches
- `agent_focus` reveals if wrong specialist was dispatched
- `tools_used` shows if Playwright was actually available for QA checks

## Guidelines

### For Passed Checks
- Briefly confirm what was verified
- Note any edge cases that were considered

### For Failed Checks
- Be specific about what failed and why
- Provide actionable feedback for fixing
- Reference specific files and lines
- Suggest solutions when possible

## After Recording

The check result will be:
1. Stored in the DB's `check_results` JSON field on the task
2. Processed by the scheduler's `process_gatekeeper_reviews()`
3. Used to determine if the task passes review or gets rejected back to the implementer
