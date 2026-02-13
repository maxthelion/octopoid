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
.octopoid/runtime/shared/queue/incoming/TASK-{uuid}.md
```

## Example

```markdown
# [TASK-f8e7d6c5] Add rate limiting to API

ROLE: implement
PRIORITY: P1
BRANCH: main
CREATED: 2024-01-15T14:30:00Z
CREATED_BY: human
EXPEDITE: false
SKIP_PR: false

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

### Example: Expedited Task (jumps queue)

```markdown
# [TASK-abc123] Fix broken login flow

ROLE: implement
PRIORITY: P1
EXPEDITE: true
...
```

### Example: Skip PR (merge directly)

```markdown
# [TASK-def456] Update architecture diagram

ROLE: implement
PRIORITY: P3
SKIP_PR: true
...
```

## After Creation

The task will be:
1. Picked up by the scheduler on next tick
2. Claimed by an agent with matching role
3. Worked on and moved to done/failed

Check status with `/queue-status`.
