# Agent Provenance Stamping on Task Creation

**Status:** Idea
**Captured:** 2026-02-15

## Raw

> Agents should put information about themselves in any tasks they create. We can then track them down. When an agent creates a task via the SDK, it should include metadata like the agent name, the parent task ID (if any), and a timestamp. This way we can trace orphaned or mystery tasks back to the agent that created them. Currently we found three "Test Task" entries with no way to identify their origin.

## Idea

When an agent creates a task (via the SDK or queue_utils), it should automatically stamp provenance metadata into the task record:

- **created_by_agent** — the agent name (e.g. `implementer-1`, `gatekeeper`)
- **parent_task_id** — the task the agent was working on when it created this new task
- **created_by_orchestrator** — which orchestrator instance created it

This makes it trivial to trace any task back to its origin, especially orphaned or mystery tasks like the three "Test Task" entries we just had to manually delete with no way to identify where they came from.

## Context

Found three "Test Task" entries in the queue (`1fdc070d`, `0225290a`, `e99447fd`) with generic titles and no metadata indicating their origin. One was even claimed by `implementer-2`. No way to tell which agent or process created them, making cleanup guesswork.

## Open Questions

- Should this be enforced at the SDK level (auto-inject from environment) or at the server level (require `created_by` fields)?
- Should the server reject task creation requests that don't include provenance info, or just log a warning?
- Do we need a `created_by` field on the task schema, or should this go into a generic `metadata` JSON column?

## Possible Next Steps

- Add `created_by_agent` and `parent_task_id` columns to the tasks schema (or a `metadata` JSON field)
- Update the SDK's `tasks.create()` to auto-inject agent identity from environment/config
- Update the server to store and return provenance fields
- Add provenance display to `/queue-status` output
