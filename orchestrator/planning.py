"""Planning escalation for failed tasks.

When a task fails multiple times, it gets escalated to planning.
A planning task is created to analyze the original task and break it
down into smaller, more achievable micro-tasks.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import get_queue_dir, is_db_enabled
from .queue_utils import parse_task_file


def create_planning_task(original_task_id: str, original_task_path: Path | str) -> str:
    """Create a planning task for a failed original task.

    The planning task asks an agent to analyze the original task and
    create a plan document with micro-tasks.

    Args:
        original_task_id: ID of the original task that failed
        original_task_path: Path to the original task file

    Returns:
        ID of the created planning task
    """
    original_task_path = Path(original_task_path)
    original_task = parse_task_file(original_task_path)

    if not original_task:
        raise ValueError(f"Could not parse original task: {original_task_path}")

    plan_id = uuid4().hex[:8]
    filename = f"TASK-{plan_id}.md"

    original_content = original_task.get("content", "")
    original_title = original_task.get("title", original_task_id)

    content = f"""# [TASK-{plan_id}] Create implementation plan for: {original_title}

ROLE: implement
PRIORITY: P1
BRANCH: {original_task.get('branch', 'main')}
CREATED: {datetime.now().isoformat()}
CREATED_BY: pre_check
ORIGINAL_TASK: {original_task_id}

## Context

The following task has failed multiple implementation attempts and needs to be
broken down into smaller, more achievable steps.

### Original Task

{original_content}

## Acceptance Criteria

- [ ] Analyze why the original task may have failed
- [ ] Create a plan document at `.orchestrator/plans/PLAN-{plan_id}.md`
- [ ] Break the task into 2-5 micro-tasks with clear acceptance criteria
- [ ] Each micro-task should be achievable in a single implementation session
- [ ] Specify dependencies between micro-tasks if any exist

## Plan Document Format

The plan document should follow this format:

```markdown
# Plan: {original_title}

## Analysis

[Why the original task may have failed, what complexity was underestimated]

## Micro-Tasks

### 1. [First micro-task title]

**Description:** [What needs to be done]

**Acceptance Criteria:**
- [ ] [Criterion 1]
- [ ] [Criterion 2]

**Dependencies:** None

### 2. [Second micro-task title]

**Dependencies:** Task 1

[etc.]
```

## Notes

- Focus on making each micro-task independently verifiable
- Consider test-first approaches where appropriate
- If the task requires changes across multiple systems, separate them
"""

    # Create the planning task file
    incoming_dir = get_queue_dir() / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    task_path = incoming_dir / filename
    task_path.write_text(content)

    # Create plans directory
    plans_dir = get_queue_dir().parent / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    # Also create in DB if enabled
    if is_db_enabled():
        from . import db
        db.create_task(
            task_id=plan_id,
            file_path=str(task_path),
            priority="P1",
            role="implement",
            branch=original_task.get("branch", "main"),
        )

    return plan_id


def parse_plan_document(plan_path: Path | str) -> list[dict[str, Any]]:
    """Parse a plan document and extract micro-tasks.

    Args:
        plan_path: Path to the plan markdown file

    Returns:
        List of micro-task dictionaries with title, description, criteria, dependencies
    """
    plan_path = Path(plan_path)
    content = plan_path.read_text()

    micro_tasks = []

    # Find all micro-task sections (### N. Title)
    task_pattern = r"###\s*(\d+)\.\s*(.+?)(?=###\s*\d+\.|## |$)"
    matches = re.findall(task_pattern, content, re.DOTALL)

    for number, task_content in matches:
        task_content = task_content.strip()

        # Extract title (first line after the header number)
        lines = task_content.split("\n")
        title = lines[0].strip() if lines else f"Micro-task {number}"

        # Extract description
        desc_match = re.search(
            r"\*\*Description:\*\*\s*(.+?)(?=\*\*|$)",
            task_content,
            re.DOTALL | re.IGNORECASE,
        )
        description = desc_match.group(1).strip() if desc_match else ""

        # Extract acceptance criteria
        criteria = []
        criteria_match = re.search(
            r"\*\*Acceptance Criteria:\*\*(.+?)(?=\*\*|$)",
            task_content,
            re.DOTALL | re.IGNORECASE,
        )
        if criteria_match:
            criteria_text = criteria_match.group(1)
            for line in criteria_text.split("\n"):
                line = line.strip()
                # Match checkbox items
                checkbox_match = re.match(r"^[-*]\s*\[[ x]\]\s*(.+)$", line)
                if checkbox_match:
                    criteria.append(checkbox_match.group(1))

        # Extract dependencies
        deps_match = re.search(
            r"\*\*Dependencies:\*\*\s*(.+?)(?=\*\*|###|$)",
            task_content,
            re.DOTALL | re.IGNORECASE,
        )
        dependencies = []
        if deps_match:
            deps_text = deps_match.group(1).strip()
            if deps_text.lower() not in ("none", "n/a", "-"):
                # Parse "Task 1" or "Task 1, Task 2" or "1, 2"
                deps = re.findall(r"(?:Task\s*)?(\d+)", deps_text, re.IGNORECASE)
                dependencies = [int(d) for d in deps]

        micro_tasks.append({
            "number": int(number),
            "title": title,
            "description": description,
            "acceptance_criteria": criteria,
            "dependencies": dependencies,
        })

    return micro_tasks


def create_micro_tasks(
    micro_tasks: list[dict[str, Any]],
    original_task_id: str,
    branch: str = "main",
    created_by: str = "planner",
) -> list[str]:
    """Create task files and DB entries for micro-tasks.

    Args:
        micro_tasks: List of parsed micro-task dicts from parse_plan_document()
        original_task_id: ID of the original escalated task
        branch: Base branch for the tasks
        created_by: Who created the tasks

    Returns:
        List of created task IDs
    """
    from .queue_utils import create_task as create_task_file

    created_ids = []
    number_to_id = {}  # Map micro-task number to actual task ID

    # First pass: create all tasks (without dependencies)
    for mt in micro_tasks:
        task_path = create_task_file(
            title=mt["title"],
            role="implement",
            context=mt["description"] or f"Micro-task from escalated task {original_task_id}",
            acceptance_criteria=mt["acceptance_criteria"] or ["Complete the task"],
            priority="P1",
            branch=branch,
            created_by=created_by,
        )

        # Extract the task ID from the filename
        task_id = task_path.stem.replace("TASK-", "")
        created_ids.append(task_id)
        number_to_id[mt["number"]] = task_id

    # Second pass: set up dependencies
    if is_db_enabled():
        from . import db

        for mt in micro_tasks:
            if mt["dependencies"]:
                task_id = number_to_id.get(mt["number"])
                if task_id:
                    # Convert dependency numbers to task IDs
                    blocker_ids = [
                        number_to_id[dep]
                        for dep in mt["dependencies"]
                        if dep in number_to_id
                    ]
                    if blocker_ids:
                        blocked_by = ",".join(blocker_ids)
                        db.update_task(task_id, blocked_by=blocked_by)

    return created_ids
