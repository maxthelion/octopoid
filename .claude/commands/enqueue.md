# /enqueue - Create New Task

Create a new task in the orchestrator queue.

## Usage

Run `/enqueue` to interactively create a task, or provide details:

```
/enqueue "Add rate limiting to API"
```

## Interactive Mode

When run without arguments, I'll ask for:

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
4. **Context** - Background and motivation
5. **Acceptance Criteria** - Specific requirements
6. **Proposed / awaiting approval?** - If the user says the task is "proposed", "not yet ready", "needs approval", "hold for now", or similar, set `blocked_by="awaiting-approval"`. The task will be created in the queue but won't be claimed by agents until a human runs `/approve-task <task-id>` to unblock it.

## Implementation

Use `create_task()` from `orchestrator.tasks` to create tasks. This function writes the task file to `.octopoid/tasks/` **and** registers it on the server in one step:

```python
from orchestrator.tasks import create_task

create_task(
    title="Add rate limiting to API",
    role="implement",
    priority="P1",
    context="Our API endpoints have no rate limiting...",
    acceptance_criteria=[
        "Rate limiting middleware added to all API routes",
        "Default limit: 100 requests per minute per IP",
        "Returns 429 Too Many Requests when exceeded",
    ],
    # branch is optional — defaults to repo.base_branch from config
)
```

### Proposed tasks (awaiting approval)

If the user indicates the task is not ready to be worked on yet — e.g. "proposed", "not yet ready", "needs approval", "park this for later", "hold off on this" — pass `blocked_by="awaiting-approval"`:

```python
create_task(
    title="Refactor authentication module",
    role="implement",
    priority="P2",
    context="...",
    acceptance_criteria=["..."],
    blocked_by="awaiting-approval",  # won't be claimed until approved
)
```

The task will appear in the queue but agents will skip it. To release it, run `/approve-task <task-id>`.

Do **not** write task files manually or place them in any queue directory. Always use `create_task()`.

## Task File Location

Tasks are written to:
```
.octopoid/tasks/TASK-{uuid}.md
```

## Example Task File

```markdown
# [TASK-f8e7d6c5] Add rate limiting to API

ROLE: implement
PRIORITY: P1
BRANCH: feature/client-server-architecture
CREATED: 2024-01-15T14:30:00Z
CREATED_BY: human

## Context
Our API endpoints have no rate limiting, making them vulnerable
to abuse and DoS attacks. We need to add rate limiting to protect
the service.

## Acceptance Criteria
- [ ] Rate limiting middleware added to all API routes
- [ ] Default limit: 100 requests per minute per IP
- [ ] Returns 429 Too Many Requests when exceeded
- [ ] Rate limit headers included in responses
- [ ] Configuration via environment variables
```

## After Creation

The task will be:
1. Registered on the server and visible in the queue immediately
2. Claimed by an agent with matching role on next scheduler tick (unless `blocked_by="awaiting-approval"` is set)
3. Worked on and moved to done/failed

If the task was created as proposed (with `blocked_by="awaiting-approval"`), it will remain unclaimed until a human runs `/approve-task <task-id>`.

Check status with `/queue-status`.

## Related Commands

- `/approve-task` — Approve a proposed task and allow agents to claim it
