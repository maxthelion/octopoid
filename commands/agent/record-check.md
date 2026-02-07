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

Use the review_utils module to record your result:

```python
import os
import sys
from pathlib import Path

# Add orchestrator to path
project_root = Path(os.environ.get("PARENT_PROJECT", "."))
sys.path.insert(0, str(project_root / "orchestrator"))

from orchestrator.review_utils import record_review_result

task_id = os.environ.get("REVIEW_TASK_ID")
check_name = os.environ.get("REVIEW_CHECK_NAME", os.environ.get("AGENT_FOCUS"))

record_review_result(
    task_id=task_id,
    check_name=check_name,
    status="pass",  # or "fail"
    summary="One-line summary of your verdict",
    details="Full markdown report with file paths and line numbers",
    submitted_by=os.environ.get("AGENT_NAME"),
)
```

Or write the JSON file directly to:
`.orchestrator/shared/reviews/TASK-{task_id}/checks/{check_name}.json`

```json
{
  "check_name": "architecture",
  "status": "pass",
  "summary": "Architecture changes are well-structured",
  "details": "### Findings\n\n- No boundary violations\n- Patterns followed correctly",
  "submitted_at": "2026-02-07T10:00:00",
  "submitted_by": "gk-architecture"
}
```

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
1. Stored in `.orchestrator/shared/reviews/TASK-{id}/checks/`
2. Aggregated by the scheduler's process_gatekeeper_reviews()
3. Used to determine if the task passes review or gets rejected back to the implementer
