# Replace requires-intervention queue with message-based actor model

**Captured:** 2026-02-27

## Raw

> requires-intervention should be an attribute on the task, not a queue

## Idea

`requires-intervention` is currently a queue in the state machine, which means tasks must transition into it from their current queue. This causes two problems:

1. **Transitions get blocked.** Task 76ce7e3f got stuck in `claimed` because `claimed -> requires-intervention` isn't a valid server transition. The circuit breaker tried to route it there after `push_branch` failed, but the server rejected it with a 400. The fixer never saw it.

2. **Positional information is lost.** When a task moves to `requires-intervention`, you no longer know where it was in the pipeline. Was it failing during `claimed -> provisional` (push issue) or `provisional -> done` (merge issue)?

### The fix: messages + blocked_by

Replace the `requires-intervention` queue with the actor model pattern we already use for other agent communication:

1. **Step fails** → post a message on the task thread addressed to `fixer` (using existing `to_actor` field) → set `blocked_by: "fixer"` on the task (using existing `blocked_by` field)
2. **Scheduler sees fixer has a message** in its inbox → spawns fixer agent
3. **Fixer does the work** → posts a reply message back to the scheduler
4. **Scheduler processes the reply** → clears `blocked_by` → retries the failed step

The task stays in its queue. No queue transition needed. The blocker prevents the scheduler from processing the task normally while the fixer works. All communication happens via the existing message system.

## Context

Task 76ce7e3f (preserve worktrees on requeue) completed successfully — agent did the work, 790 tests pass, outcome inferred as `done`. The `claimed -> provisional` flow ran `rebase_on_base` (success) then `push_branch` (failed — branch already existed on remote from a previous run). The circuit breaker tried `fail_task()` → `request_intervention()`, but the server rejected `claimed -> requires-intervention`. Task stuck in `claimed` with no agent and no fixer visibility.

Adding `X -> requires-intervention` for every queue is whack-a-mole. The message-based approach is the structural fix — it uses existing infrastructure (`to_actor`, `parent_message_id`, `blocked_by`) with no schema changes.

## Design

### Server changes

- **Tasks**: add `needs_intervention` boolean (default false). The state lives on the task — fast to query, explicit.
- **Messages**: already have `from_actor`, `to_actor`, `type`, `parent_message_id`. The context lives in messages.
- Query: `GET /api/v1/tasks?needs_intervention=true` for scheduler, `GET /api/v1/messages?to_actor=fixer&unreplied=true` for fixer context.

### Orchestrator changes

- `request_intervention()` → set `needs_intervention=true` on task, post message with `to_actor=fixer` containing failure context
- Scheduler tick: query for tasks with `needs_intervention=true`, spawn fixer
- Fixer agent: read the message, fix the issue, post reply to scheduler
- Scheduler: on receiving fixer reply, clear `needs_intervention`, retry the failed step
- Remove `requires-intervention` queue from flows and `fail_task()` routing

### `failed` stays as a terminal queue

`failed` is a true terminal state (fixer also failed, or unrecoverable error). It stays as a queue. The message-based approach replaces only the "fixable" intervention path.

## Open Questions

- Should the fixer claim the task while working, or does `blocked_by=fixer` suffice to prevent other agents from touching it?
- What message `type` for intervention requests? `intervention_request`? Or just use `to_actor=fixer` as the signal?

## Server Task

See `project-management/tasks/octopoid-server/intervention-via-messages.md` — just needs unreplied-message query endpoint.

## Possible Next Steps

- Server: add unreplied message query (see server task)
- Orchestrator: update `request_intervention()` to use messages + `blocked_by`
- Orchestrator: update scheduler to spawn fixer from message inbox
- Orchestrator: update fixer agent to read messages and post replies
- Orchestrator: remove `requires-intervention` queue from flows
- Migrate any tasks currently in `requires-intervention`
