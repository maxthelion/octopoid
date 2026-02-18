# /enqueue - Create New Task

Create a new task in the orchestrator queue.

## Implementation

Use `orchestrator.tasks.create_task()` which handles branch defaulting, file writing, and server registration.

```python
from orchestrator.tasks import create_task

task_path = create_task(
    title=title,
    role=role,
    context=context,
    acceptance_criteria=acceptance_criteria,
    priority=priority,
    # branch is optional — defaults to get_base_branch() from config.yaml
    # Only pass branch if the user explicitly specifies one
)
```

If the user specifies a branch explicitly, pass it:

```python
task_path = create_task(
    title=title,
    role=role,
    context=context,
    acceptance_criteria=acceptance_criteria,
    priority=priority,
    branch=branch,  # only if user specified
)
```

### With an existing task file

If the user provides a path to an existing task file, copy it to `.octopoid/tasks/` first, then register via SDK:

```python
import shutil
shutil.copy(source_path, f'.octopoid/tasks/{task_id}.md')
```

### For project tasks

If creating a task for a project, include `project_id`:

```python
task_path = create_task(
    title=title,
    role=role,
    context=context,
    acceptance_criteria=acceptance_criteria,
    priority=priority,
    project_id=project_id,
    blocked_by=blocked_by,  # previous task in chain
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
4. **Branch** - Base branch (default: `get_base_branch()` from config.yaml). Only ask if the user might want a non-default branch.
5. **Context** - Background and motivation
6. **Acceptance Criteria** - Specific requirements

## Turn Budget

When creating a task, estimate the appropriate `max_turns` based on the work involved. This overrides the agent's default (150 turns) and saves cost on lightweight tasks.

Guidelines:
- **10-20 turns**: Rebases, cherry-picks, trivial one-line fixes
- **30-50 turns**: Small focused changes (1-2 files), adding a field, writing a test
- **50-80 turns**: Medium tasks (2-3 files), refactors within one module
- **80-120 turns**: Larger features, multi-file changes with tests
- **150 (default)**: Only for complex tasks where you genuinely can't predict scope

Set it via `max_turns` on the task. If the user specifies turns explicitly, use their value. Otherwise, make your best estimate based on the task description.

**Note:** This requires the per-task max_turns feature (TASK-19bbcaa6). Until that lands, this field will be ignored by the scheduler.

## Rules

- **Always use `create_task()`** — it handles branch defaulting, file creation, and server registration
- **Do NOT pass `branch` unless the user explicitly specifies one** — the default comes from `config.yaml`
- **Always write task files to `.octopoid/tasks/`** — never point at files elsewhere
- The `file_path` field in the API should be relative to the project root (e.g. `.octopoid/tasks/TASK-abc123.md`)

## After Creation

The task will be:
1. Picked up by the scheduler on next tick
2. Claimed by an agent with matching role
3. Worked on and moved to done/failed

Check status with `/queue-status`.
