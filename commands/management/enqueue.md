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
4. **Branch** - Base branch (usually `main`)
5. **Context** - Background and motivation
6. **Acceptance Criteria** - Specific requirements

### Optional Fields (I'll infer these or ask if needed)

7. **Expedite** - Should this task jump the queue?
   - Use for urgent tasks that need immediate attention
   - Expedited tasks are processed before all non-expedited tasks
   - Best guess: Set `true` if title contains "urgent", "fix", "broken", "critical"

8. **Skip PR** - Should this skip PR creation and merge directly?
   - Use for: docs/plans, submodule updates, low-risk changes
   - Best guess: Set `true` if task modifies only docs/plans/configs

## Task File Location

Tasks are created in:
```
.octopoid/tasks/TASK-{id}.md
```

And registered with the server API for agent claiming.

## Example

```markdown
---
id: TASK-f8e7d6c5
title: "Add rate limiting to API"
priority: P1
role: implement
queue: incoming
created_by: human
created_at: 2024-01-15T14:30:00Z
---

# Add rate limiting to API

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

## How It Works

When you run `/enqueue`, the system:
1. Generates a unique task ID
2. Registers the task with the server API (so agents can claim it)
3. Creates a local markdown file in `.octopoid/tasks/` (the full task description)
4. Places the task in the `incoming` queue

The task will then be:
1. Picked up by the scheduler on next tick
2. Claimed by an agent with matching role
3. Worked on and moved to provisional/done/failed

Check status with `/queue-status`.
