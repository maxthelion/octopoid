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

## Implementation

Use `create_task()` from `octopoid.tasks` to create tasks. This function writes the task file to `.octopoid/tasks/` **and** registers it on the server in one step:

```python
from octopoid.tasks import create_task

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

Do **not** write task files manually or place them in any queue directory. Always use `create_task()`.

## Invariant coverage (when enqueuing from a draft)

When creating tasks from a draft that has an `## Invariants` section, check whether the tasks you're creating collectively cover the invariants. This doesn't mean every task must fully satisfy every invariant — partial progress is fine. But the gap should be explicit.

After creating tasks, summarise:
- Which invariants the task(s) fully address
- Which invariants are only partially covered (and what remains)
- Which invariants are not covered at all by this batch of tasks

If important invariants are not covered, suggest additional tasks or flag them to the user. The goal is that by the time all tasks from a draft are done, the invariants should be met. If a single task can't cover an invariant, say so — don't silently drop it.

Also check `project-management/system-spec/` — if the draft's invariants overlap with existing spec entries, reference them. If they're new, they'll be added to the spec when `/process-draft` confirms they're met.

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
2. Claimed by an agent with matching role on next scheduler tick
3. Worked on and moved to done/failed

Check status with `/queue-status`.
