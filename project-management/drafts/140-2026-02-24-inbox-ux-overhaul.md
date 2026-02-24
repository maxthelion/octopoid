# Inbox UX Overhaul: Human-Readable Messages, Threads, and Sent Tab

**Status:** Idea
**Captured:** 2026-02-24

## Raw

> The inbox is not super informative at the moment. When an agent has suggested a draft, it should be pulled in to the view on the right with the suggested actions for it. There should be a human readable title in the list on the left. That view should also have relative time since it was created. If an agent has been asked to do something via an action button: It should be listed in a "sent" tab at the top. If the response to a request is a series of actions and decisions, this should be presented in a sensible way. Eg if I ask for a draft to be processed, the agent should present feedback such as: this can be archived, or shall I enqueue the tasks? Or There are open questions, would you like to answer them? Responding to these should reference the whole message chain as a thread.

## Idea

The inbox tab currently shows raw JSON and cryptic prefixes (PROP, RSLT). It needs to be a proper human-facing inbox with:

### 1. Human-readable message list (left panel)

**Current:** `PROP architecture-analyst: json {  "entityty...`
**Wanted:** `Architecture Analyst — New draft: CI status checks  · 3h ago`

- Parse the message content and generate a human-readable title
- Show relative timestamps ("3h ago", "yesterday")
- Use agent name, not raw role prefix
- Differentiate message types visually (proposals, results, errors)

### 2. Rich message detail (right panel)

When a message references an entity (draft, task, project), pull in the actual content:

- **Draft proposals:** Show draft title, status, summary, and the open questions if any. Action buttons: "Process Draft", "Archive", "Enqueue Tasks"
- **Task results:** Show what the agent did, PR link if created, test results
- **Errors:** Show the error clearly with context about what was attempted

### 3. "Sent" tab

When you trigger an action (e.g. press a button to process a draft), it currently fires and disappears. There should be a "Sent" sub-tab that shows:

- What action was requested
- When it was sent
- Status: pending / running / completed / failed
- The agent's response when it comes back

### 4. Threaded conversations

When an action agent responds with follow-up questions or decisions, these should form a thread:

```
You: Process Draft #96
Agent: Draft #96 has 3 open questions:
  1. Should we use Redis or in-memory caching?
  2. What's the target latency?
  3. Is this P1 or P2?
  → Would you like to answer these before I enqueue tasks?
You: Use in-memory, 100ms, P2
Agent: Created TASK-abc123 "Add in-memory caching layer"
```

Each response in the thread should reference the full chain, so the action agent has context.

### 5. Structured action responses

When `/process-draft` runs, the agent should return structured feedback rather than a wall of text:

- "This draft can be archived — all items are complete" → [Archive] button
- "Found 2 proposed tasks" → [Enqueue All] [Review Each] buttons
- "3 open questions need answers before this is actionable" → [Answer Questions] with inline form
- "Extracted 2 rules for CLAUDE.md" → [Review & Apply] button

## Context

The inbox was recently built (Draft #78, #80, #81) as a minimal first pass — raw messages from agents with basic action buttons. It works but is hard to use: messages are raw JSON, there's no threading, and you can't track what you've sent. The screenshot shows the current state — RSLT errors, PROP messages with truncated JSON, no timestamps, no context.

## Open Questions

- How should threads be stored? Server-side message threading (parent_id field) or client-side grouping?
- Should the "Sent" tab be a sub-tab within Inbox, or a separate top-level tab?
- How much of this is dashboard work vs server/dispatcher work? The structured responses from action agents would need changes to the message_dispatcher.
- Should we define a message schema (type, title, body, entity_ref, actions) that agents write to, or keep it freeform and parse on the dashboard side?

## Possible Next Steps

- Define a message display schema: `{title, summary, entity_type, entity_id, timestamp, actions[]}`
- Update `message_dispatcher.py` to write structured results instead of raw JSON
- Update `packages/dashboard/tabs/inbox.py` to render human-readable messages
- Add relative timestamps (can use a simple "time ago" helper)
- Add a "Sent" sub-tab tracking dispatched actions
- Add threading support (probably needs a `parent_message_id` field on messages)
