# Actor model: replace result.json with messages for all agent types

**Captured:** 2026-02-27

## Raw

> /draft-idea for replacing result.json more generally - eg implementers and gatekeepers. Making the system more actor-ish

## Idea

Move from file-based result passing (`result.json`) to message-based communication for all agent types. Agents post a structured message to the server when they complete. The scheduler subscribes to these messages and processes them. This shifts the architecture from "pure functions writing files" toward an **actor model** where agents and the scheduler communicate through messages.

## Context

Currently, agents are pure functions: they do work and write a `result.json` file to disk. The scheduler polls the filesystem to detect agent completion (PID exit + result.json exists), reads the file, and processes the result.

This works, but has friction:

- **Stale result.json** — a previous run's result.json can be picked up by the scheduler, causing a task to be processed with the wrong result (see postmortem 2026-02-15). We've added workarounds (archiving old results) but the root cause is that files are stateless — they don't know which run produced them.
- **No acknowledgement** — the agent writes the file and exits. It has no confirmation that the scheduler received the result. If the scheduler is down, the result sits on disk until the next tick.
- **Filesystem coupling** — the scheduler must have access to the agent's filesystem. This works on a single machine but prevents running agents on remote machines that don't share a filesystem with the scheduler.
- **Two communication channels** — agents write files, humans write messages. The scheduler processes both through different codepaths. Unifying these simplifies the architecture.

## Current flow (file-based)

```
Agent                    Filesystem              Scheduler
  |                         |                       |
  |-- does work ----------->|                       |
  |-- writes result.json -->|                       |
  |-- exits                 |                       |
  |                         |<-- polls PID + file --|
  |                         |-- reads result.json ->|
  |                         |                       |-- processes result
```

## Proposed flow (message-based)

```
Agent                    Server                  Scheduler
  |                         |                       |
  |-- does work             |                       |
  |-- POST /messages ------>|                       |
  |-- exits                 |                       |
  |                         |<-- polls messages ----|
  |                         |-- returns message --->|
  |                         |                       |-- processes result
```

### What changes

1. **Agent writes result.json AND posts a message.** During the transition, agents do both — the file is the fallback, the message is the primary channel. The scheduler prefers the message but falls back to the file.

2. **Eventually, drop result.json.** Once messages are reliable, agents only post messages. No more filesystem coordination.

3. **Scheduler listens for messages instead of polling files.** On each tick, the scheduler fetches unprocessed messages for its orchestrator and processes them. PID tracking becomes optional (used only for process management, not result detection).

### Message format

```json
{
  "task_id": "2a06729d",
  "agent_name": "implementer-1",
  "type": "agent_result",
  "content": {
    "outcome": "done",
    "commits": 3,
    "turns_used": 45
  }
}
```

For gatekeepers:

```json
{
  "task_id": "2a06729d",
  "agent_name": "sanity-check-gatekeeper-1",
  "type": "agent_result",
  "content": {
    "status": "success",
    "decision": "approve",
    "comment": "Code looks good. All tests pass."
  }
}
```

For fixers:

```json
{
  "task_id": "2a06729d",
  "agent_name": "fixer-1",
  "type": "agent_result",
  "content": {
    "outcome": "fixed",
    "diagnosis": "git pull --rebase failed due to unpushed local commits",
    "fix_applied": "ran git pull --rebase"
  }
}
```

Same structure for all agent types — the `content` varies by role but the envelope is consistent.

## Benefits

- **No stale results.** Messages are timestamped and tied to a specific agent run. Can't accidentally process a message from a previous run.
- **Auditable.** Every result is a message in the task's thread. You can see the full history: agent posted result → scheduler processed it → posted review → merged PR.
- **Remote-friendly.** Agents on remote machines can post messages to the server without sharing a filesystem with the scheduler. This unblocks true distributed execution.
- **Unified communication.** Humans, agents, and the scheduler all communicate through the same messages system. One codepath for processing all inputs.
- **Decoupled.** The scheduler doesn't need to know where the agent ran or have access to its filesystem. It just reads messages.

## Risks

- **Server dependency.** If the server is down when the agent finishes, the message is lost. Mitigation: agent writes result.json as a local fallback; scheduler checks both channels.
- **Relaxes pure function constraint.** Agents currently have zero side effects. Posting a message is a side effect. This is a deliberate architectural choice — we're trading purity for better communication.
- **Migration complexity.** Changing all agent types at once is risky. Need a phased approach with dual-write (file + message) during transition.

## Migration path

1. **Phase 1: Scheduler posts results as messages** (current plan for fixer agent, draft #170). No agent changes — scheduler reads result.json and posts it as a message for audit. Validates that the messages infrastructure works.
2. **Phase 2: Agents dual-write.** Agents write result.json AND post a message. Scheduler prefers the message but falls back to the file. This is backwards-compatible.
3. **Phase 3: Drop result.json.** Once dual-write is proven stable, agents only post messages. Remove file-based result detection from the scheduler.

## Open Questions

- Does the server need a dedicated "agent results" message type, or is the general messages table sufficient?
- How does the agent authenticate to post a message? Currently agents have no API credentials. Does the scheduler provision a scoped token when spawning?
- Should the message be posted by the agent process itself, or by a small wrapper script that runs after the agent exits (keeping the agent itself pure)?

## Relationship to other drafts

- **Draft #170** (self-healing fixer): Phase 1 of this migration — scheduler posts result.json as a message for the fixer agent
- **Draft #169** (force-queue endpoint): Admin recovery endpoint, orthogonal but benefits from message audit trail
- **Draft #168** (unified logging): Logging and messages are complementary — logs for debugging, messages for task lifecycle


## Invariants

- `agents-communicate-via-messages`: Agents communicate their completion state to the orchestrator via a server-side message (or stdout), not by writing files to disk. The scheduler does not need to poll the filesystem to detect agent results.
