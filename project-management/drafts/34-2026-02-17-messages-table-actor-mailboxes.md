# Messages Table: Actor Mailboxes for Agent Communication

**Status:** Idea
**Captured:** 2026-02-17
**Related:** Draft 31 (agents as pure functions / actor model), Draft 33 (roadmap), Draft 30 (why octopoid keeps breaking)

## Raw

> In the draft about the pure functions with actors, is there an improvement to be had if we had a messages table in the octopoid server? What would that unlock?

## Idea

Add a `messages` table to the octopoid server that formalizes the actor model's core primitive — message passing — as durable, queryable data. Currently, communication between agents and the orchestrator is scattered across result.json files (disk, ephemeral), PR comments (GitHub), task file rewrites (lossy), stderr logs (disk), and state.json (disk). A messages table unifies all of this.

### What's scattered today

| Communication | Where it lives | Problems |
|---|---|---|
| Agent result (success/fail) | `result.json` on disk | Ephemeral, deleted on cleanup, stale files cause bugs (draft #24) |
| Rejection feedback | Task file rewrite + PR comment | Lossy — rewriting destroys previous version, mashed with original instructions |
| Test failure feedback | Task re-queue with rewritten file | Context lost — new agent session starts fresh, doesn't know what was tried |
| Gatekeeper review | PR comment + state transition | Split across two systems (GitHub + server) |
| "Why did this fail?" | stderr.log, state.json, PR comments, task_history | 5 places to look, none complete |

### Schema

```sql
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    from_actor TEXT NOT NULL,   -- "orchestrator", "implementer-1", "gatekeeper", "human"
    to_actor TEXT,              -- target actor (NULL = broadcast/log entry)
    type TEXT NOT NULL,         -- "result", "feedback", "rejection", "instruction", "test_output", "advisory"
    content TEXT NOT NULL,      -- JSON blob
    created_at TEXT DEFAULT (datetime('now')),
    scope TEXT                  -- multi-tenancy
);

CREATE INDEX idx_messages_task ON messages(task_id, created_at);
CREATE INDEX idx_messages_to ON messages(to_actor, created_at);
CREATE INDEX idx_messages_scope ON messages(scope);
```

### What it unlocks

#### 1. Mid-flight feedback loops

Draft #31's hardest open question: how does the orchestrator give mid-flight feedback to a running agent? Currently the agent finishes, orchestrator runs tests, task gets requeued, a new agent session starts from scratch — losing all context of what was tried.

With messages, the orchestrator posts test results while the agent is still alive:

```
orchestrator → implementer: {type: "instruction", content: {task_description: "..."}}
implementer → orchestrator: {type: "result", content: {status: "success"}}
orchestrator → implementer: {type: "test_output", content: {passed: false, failures: ["test_foo: AssertionError..."]}}
implementer → orchestrator: {type: "result", content: {status: "success", notes: "fixed test_foo"}}
orchestrator → implementer: {type: "test_output", content: {passed: true}}
```

The agent polls for messages. The orchestrator runs tests externally and posts results. No session restart, no context loss.

#### 2. Rejection context across sessions

Currently: gatekeeper rejects → task file gets rewritten → new implementer starts fresh. The rejection reason, original task, and what was tried are mashed into one file.

With messages, each attempt is preserved:

```
SELECT * FROM messages WHERE task_id = 'TASK-xxx' ORDER BY created_at;

[1] orchestrator → implementer-1: {type: "instruction", content: "implement feature X..."}
[2] implementer-1 → orchestrator: {type: "result", content: {status: "success"}}
[3] orchestrator → gatekeeper: {type: "instruction", content: {pr_diff: "...", task: "..."}}
[4] gatekeeper → orchestrator: {type: "result", content: {decision: "reject", comment: "missing edge case for empty input"}}
[5] orchestrator → implementer-2: {type: "instruction", content: "fix: missing edge case for empty input. Previous attempt created PR #67."}
```

The second implementer gets the full thread — what was asked, what was done, why it failed.

#### 3. Results move off disk

`result.json` is the #1 source of stale-state bugs (draft #24: "failed despite completing"). It's a file on disk that gets orphaned, cached, or deleted at the wrong time. With messages, the agent posts its result to the server:

```python
# Agent finishes and posts result
sdk.messages.create(
    task_id=task_id,
    from_actor=agent_name,
    to_actor="orchestrator",
    type="result",
    content=json.dumps({"status": "success", "decision": "approve", "comment": "..."})
)
```

- Durable — survives process crashes
- Queryable — "show me all gatekeeper rejections this week"
- No stale files — nothing on disk to get out of sync
- Another orchestrator instance could pick up where one left off

#### 4. Unified audit trail

"What happened to TASK-xxx?" becomes one query instead of checking 5 places:

```sql
SELECT from_actor, type, content, created_at
FROM messages WHERE task_id = ? ORDER BY created_at;
```

This replaces: task_history (server, events only), PR comments (GitHub), result.json (disk), stderr.log (disk), state.json (disk). Everything in one place.

#### 5. Backpressure signal

In the actor model, mailbox depth = backpressure. If an agent has 5 unread "fix these test failures" messages on the same task, something is stuck. The orchestrator detects this and escalates to human instead of looping forever. Currently `consecutive_failures` is a crude proxy — messages make it precise.

### API endpoints

```
POST   /api/v1/messages              — create a message
GET    /api/v1/messages?task_id=X     — list messages for a task
GET    /api/v1/messages?to_actor=X    — list unread messages for an actor
GET    /api/v1/tasks/:id/messages     — messages for a specific task
```

### SDK additions

```python
class MessagesAPI:
    def create(self, task_id, from_actor, type, content, to_actor=None):
        ...
    def list(self, task_id=None, to_actor=None, type=None):
        ...

# Usage in pure-function orchestrator:
sdk.messages.create(
    task_id="TASK-xxx",
    from_actor="gatekeeper",
    to_actor="orchestrator",
    type="result",
    content=json.dumps(result)
)
```

### What it does NOT replace

- **Task state machine** — messages are communication, not state. `incoming→claimed→provisional→done` stays on the tasks table.
- **Task files** — the `.octopoid/tasks/TASK-xxx.md` file remains the canonical task description. Messages are the conversation *about* the task.
- **PR comments** — the orchestrator still posts review comments to GitHub for human visibility. But the source of truth moves to messages; PR comments become a projection.

## Timing: sooner or later?

See "Context" below.

## Context

This came up while reviewing draft #31 (agents as pure functions) and the actor model connection. The messages table makes the actor mailbox pattern explicit in the data model. It directly addresses several open questions from draft #31:

- "How does the orchestrator handle long-running agents that need mid-flight feedback?" → messages
- "Does this eliminate the need for finish and fail scripts?" → yes, agents post result messages
- "What does the result.json schema look like?" → it's a message of type "result"

It also addresses draft #30's "state scattered everywhere" problem — messages become the single source of truth for all agent↔orchestrator communication.

## Open Questions

- Should messages be immutable (append-only log) or editable? Append-only is simpler and matches the actor model (you can't un-send a message).
- Should agents read messages via SDK polling, or should the orchestrator pass messages as input when spawning? Polling adds complexity; passing as input is simpler but only works at session start.
- How does this interact with the `task_history` table that already records events? Merge them, or keep both (history = state transitions, messages = communication)?
- Should there be a `read_at` / `acknowledged` field for tracking what the recipient has seen?

## Possible Next Steps

- Add migration `0010_add_messages.sql` with schema above
- Add `/api/v1/messages` routes to server
- Add `MessagesAPI` to Python SDK
- Replace `result.json` with message posting in the pure-function gatekeeper (draft #29)
- Replace task file rewriting on rejection with a message thread
