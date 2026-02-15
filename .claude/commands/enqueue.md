# /enqueue - Create New Task

Create a new task in the orchestrator queue.

## Implementation

Use the Python SDK to create tasks on the server. Always write the task file to `.octopoid/tasks/` — the scheduler expects files there.

```python
from orchestrator.queue_utils import get_sdk
import uuid

sdk = get_sdk()

# Generate task ID and file path
task_id = f"TASK-{uuid.uuid4().hex[:8]}"
file_path = f".octopoid/tasks/{task_id}.md"

# Write the task markdown file FIRST
task_content = f"""# [{task_id}] {title}

ROLE: {role}
PRIORITY: {priority}
BRANCH: {branch}
CREATED: {datetime.now(timezone.utc).isoformat()}
CREATED_BY: human

## Context
{context}

## Acceptance Criteria
{acceptance_criteria}
"""

# Write to .octopoid/tasks/
import os
os.makedirs('.octopoid/tasks', exist_ok=True)
with open(file_path, 'w') as f:
    f.write(task_content)

# THEN create on the server — file_path is relative to project root
result = sdk.tasks.create(
    id=task_id,
    file_path=file_path,
    title=title,
    role=role,
    priority=priority,
    queue='incoming',
    branch=branch,
)
```

### With an existing task file

If the user provides a path to an existing task file, copy it to `.octopoid/tasks/` first:

```python
import shutil
shutil.copy(source_path, f'.octopoid/tasks/{task_id}.md')
```

### For project tasks

If creating a task for a project, include `project_id`:

```python
result = sdk.tasks.create(
    id=task_id,
    file_path=file_path,
    title=title,
    role=role,
    priority=priority,
    project_id=project_id,
    blocked_by=blocked_by,  # previous task in chain
    queue='incoming',
    branch=branch,
)
```

## Scope Assessment

Before creating a task, assess whether the work described is a good fit for a **single task** or whether it should be a **project** (multiple sequential tasks).

A task is too large for a single agent if it:
- Touches more than 2-3 files across different subsystems
- Requires changes in multiple codebases (e.g. server TypeScript + Python orchestrator)
- Has more than 3 distinct implementation steps
- Would need a long task file with multiple code samples

If the work is better served by a project, suggest this to the user:
> "This looks like it spans multiple subsystems — would you like me to create a project with separate tasks instead? That way each agent gets a focused, achievable piece."

Then use `sdk.projects.create()` and create individual tasks with `project_id` and appropriate `blocked_by` chains.

## Interactive Mode

When run without arguments, ask for:

1. **Title** - Brief, descriptive title
2. **Role** - Who should handle this:
   - `implement` - Code changes
   - `test` - Testing tasks
   - `review` - Code review
3. **Priority** - How urgent:
   - `P0` - Critical (security, broken builds)
   - `P1` - High (important features)
   - `P2` - Normal (default)
   - `P3` - Low (nice-to-have)
4. **Branch** - Base branch (default: current working branch)
5. **Context** - Background and motivation
6. **Acceptance Criteria** - Specific requirements

## Rules

- **Always write task files to `.octopoid/tasks/`** — never point at files elsewhere
- Use the Python SDK, not the CLI (the CLI has a silent fallback bug)
- The `file_path` field in the API should be relative to the project root (e.g. `.octopoid/tasks/TASK-abc123.md`)

## After Creation

The task will be:
1. Picked up by the scheduler on next tick
2. Claimed by an agent with matching role
3. Worked on and moved to done/failed

Check status with `/queue-status`.
