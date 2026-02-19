# Lightweight Actor Agents with Dashboard-Triggered Actions

**Status:** Idea
**Captured:** 2026-02-19
**Related:** Draft 34 (messages table / actor mailboxes)

## Raw

> lightweight agents that use the actor pattern. There can be buttons in the dashboard that write messages to their inbox and cause them to be invoked. An example might be a process draft button on a draft page. Or a button on a task to stop work on it. Some agents might be programmatic, others LLMs. I also imagine that other agents could run in the background to propose draft ideas and even draft tasks. An agent might create a draft and attach a bunch of actions to it: turn this into a project, archive this etc. Choosing an option would send it to an inbox for agents. Likewise, an agent could look at drafts in the background and create a bunch of actions on them, like "archive this, it's out of date" or "move these points to a new draft" or "enqueue the rest of the work as a project". That way the system is more proactive about finding the right work to be doing

## Idea

Two-part concept: (1) lightweight agents that respond to messages in their inbox, and (2) a proactive layer where background agents propose actions that humans can approve with one click.

### Part 1: Dashboard buttons → actor inboxes

The dashboard becomes an action surface, not just a viewer. Buttons on entities write messages to agent inboxes:

- **Draft page**: "Process this draft" → message to a draft-processor agent
- **Task card**: "Stop work" → message to the scheduler to release the task
- **Task card**: "Retry" → message to requeue
- **Draft page**: "Turn into project" → message to a project-creator agent

Agents can be:
- **Programmatic** — simple state transitions, no LLM needed (archive, requeue, stop)
- **LLM-powered** — need reasoning (process a draft into tasks, break down a feature)

### Part 2: Proactive background agents that propose actions

Background agents run periodically and attach proposed actions to entities:

- A **draft curator** scans drafts and proposes: "archive this, it's out of date", "merge these two drafts", "enqueue the rest as a project"
- A **task proposer** looks at the codebase, recent failures, or open issues and drafts new tasks
- A **draft generator** captures patterns from conversations and creates draft ideas

These proposed actions appear as buttons on the entity in the dashboard. The human reviews and clicks to approve — which sends the action message to the appropriate agent's inbox.

### The pattern

```
Background agent → proposes actions on entity → human sees buttons in dashboard
                                                      ↓ clicks
                                              message to agent inbox
                                                      ↓
                                              agent executes action
```

This makes the system proactive about finding the right work while keeping humans in the approval loop.

## Context

Came up while discussing dashboard improvements and the drafts tab redesign. The current system is reactive — humans enqueue tasks, agents execute. This idea makes the system actively suggest work, surface stale items, and let humans approve actions with minimal friction. Builds on the actor mailbox infrastructure from Draft 34.

## Open Questions

- How are proposed actions stored? A new `actions` table? Or as messages with a special type?
- How do actions get rendered as buttons in the dashboard? Does the entity (draft/task) carry a list of available actions?
- What's the invocation model for lightweight agents? Always-running daemon? Spawned on message receipt? Scheduled poll?
- How does this relate to the existing scheduler? Are these a new category of agent, or do they use the same pool/blueprint model?
- Should proposed actions expire? (e.g. "archive this" proposed 2 weeks ago may no longer be relevant)

## Possible Next Steps

- Design the action data model (what an "action" looks like, how it's attached to entities)
- Prototype one end-to-end flow: background agent proposes "archive draft" → button appears in dashboard → click sends message → draft gets archived
- Extend Draft 34's messages table with action-specific fields (or create a separate actions table)
- Identify the first 3-5 useful lightweight agents to build
