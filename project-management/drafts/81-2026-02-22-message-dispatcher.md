# Message Dispatcher: Orchestrator Polls Inbox and Spawns Action Agents

**Status:** Idea
**Captured:** 2026-02-22
**Related:** Draft 50 (lightweight actor agents), Draft 68 (actions as agent instructions), Draft 78 (human inbox tab)

## Raw

> We just need to get the orchestrator to read from that inbox, spawn a generic agent as a pure function that sends a message when complete, and then orchestrator cleans up the message if successful or escalates if not.

## Idea

The scheduler gains a message dispatch loop. Each tick, it polls the server for unprocessed `action_command` messages (e.g. `sdk.messages.list(to_actor="agent", type="action_command")`). For each message, it spawns a lightweight agent as a pure function — the agent receives the message content as its prompt, does the work, and posts a result message back. The orchestrator then:

- **On success:** cleans up the original message (marks it done / deletes it) and posts a `worker_result` message to the human inbox
- **On failure:** escalates by posting a warning/error message to the human inbox

The agent is stateless and disposable — no task lifecycle, no worktree, no PR. It's a pure function: message in → work done → message out.

## Context

The dashboard already posts `action_command` messages to the server when the human clicks draft action buttons (Enqueue, Process, Archive). The server messages API works. But nothing reads those messages. This draft closes the loop.

This is a much simpler pattern than the full task lifecycle. Action agents don't need worktrees, branches, PRs, or gatekeepers. They run in the main repo context (read-only for most actions) and their output is a message, not a code change.

## Design

### Execution environment

Action agents run in the main repo working directory — no worktree, no branch, no PR. They are short-lived pure functions.

**Allowed:** Read any file, SDK calls (server API), write files under `project-management/` (drafts, proposed tasks, etc.)
**Not allowed:** Git operations, writes outside `project-management/`, long-running work.

This works because:
- Most actions are API calls (update draft status, create task, post messages)
- File writes are limited to project management artifacts (drafts, proposed tasks), not source code
- Task content will move to the server (draft 82), so `create_task()` won't need local file writes long-term
- No git means no contamination risk — the worktree index leak problem doesn't apply

### Scheduler loop (new step in each tick)

```
1. Poll: sdk.messages.list(to_actor="agent", type="action_command")
2. For each unprocessed message:
   a. Mark message as "processing" (claim it)
   b. Spawn a lightweight agent with the message content as prompt
   c. Agent does the work and posts result via sdk.messages.create(type="worker_result", to_actor="human")
   d. On success: mark original message as done
   e. On failure: post error message to human inbox, mark original as failed
3. Process one message per tick (serial, not parallel) to keep things simple
```

### Agent invocation

The agent is a single `claude -p` call with:
- The message content as the prompt
- A system prompt listing available tools and constraints
- Access to: SDK, file reads, writes scoped to `project-management/`
- `--max-turns 10` (these should be quick operations)
- `--allowedTools` to enforce the read/write/SDK constraints
- Working directory: repo root

### Message lifecycle

Messages get a `status` field: `pending` → `processing` → `done` / `failed`.

The "processing" state acts as a claim — if the scheduler sees a message in "processing", it skips it. If a message stays in "processing" too long (agent crashed), the scheduler can reset it to "pending" for retry or mark it "failed" and escalate.

## Open Questions

- Does the server messages API support a `status` field yet, or does it need adding?
- What's the system prompt template for action agents? Needs to list available skills/tools clearly
- Should we rate-limit? (Starting with 1 per tick is probably enough)
- How long before a "processing" message is considered stuck? (5 minutes?)

## Possible Next Steps

- Add `status` field to server messages table (pending/processing/done/failed)
- Add message polling to the scheduler tick
- Create the system prompt template for action agents
- Implement the spawn → result → cleanup cycle
- Test with the existing "archive draft 80" message sitting on the server
