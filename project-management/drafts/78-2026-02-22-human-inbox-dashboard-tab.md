---
**Processed:** 2026-02-22
**Mode:** human-guided
**Actions taken:**
- Resolved all open questions
- Keeping in drafts/ — ready to enqueue when desired
**Outstanding items:** Enqueue as task(s)
---

# Human Inbox Tab in Dashboard

**Status:** Idea
**Captured:** 2026-02-22
**Related:** Draft 50 (lightweight actor agents), Draft 34 (messages table)

## Raw

> A human inbox for agents to send messages to. It should be in the dashboard. Some messages should refer to actions that can be taken. For example, a proposer agent might create a draft and send a message to the human inbox with actions presented inline for enqueuing work etc. Messages as a list on the left, contents on the right. Similar to drafts view. Messages ordered by recency. Re-use the messages table on the server if it works.

## Idea

A new dashboard tab that acts as a human inbox — a place where agents can send messages that the human sees and acts on. The layout mirrors the drafts tab: message list on the left (ordered by recency, newest first), message content on the right. Some messages are purely informational, others carry inline actions (buttons) that the human can click to trigger work — e.g. "Enqueue this as a task", "Archive this draft", "Approve this proposal".

The server already has a messages table. Reuse it — actions are embedded in the `content` JSON field, no schema changes needed.

## Context

Currently agents communicate with humans through task results, PR comments, and the local filesystem inbox (`orchestrator/message_utils.py`). There's no unified place in the dashboard to see agent messages or act on them. The drafts tab already posts messages via `_post_inbox_message()`, but those go to the local filesystem — not visible in the dashboard itself. This draft proposes making those messages first-class in the UI.

## Resolved Questions

- **Action metadata:** Embedded in the `content` JSON field. No new server columns needed. The dashboard parses actions out of the content.
- **Read/unread state:** No. Keep it simple — recency is enough.
- **Predefined vs freeform actions:** Freeform — agents specify label + message payload in the content JSON. Dashboard renders whatever it finds.
- **Replace local filesystem messages?** Yes. Once the inbox tab works, remove `orchestrator/message_utils.py` and the local file-based message system. All messages go through the server API.

## Server Messages API (already exists)

Schema: `id`, `task_id`, `from_actor`, `to_actor`, `type`, `content`, `created_at`, `scope`

Types in use: `action_proposal`, `action_command`, `worker_result`

SDK: `sdk.messages.list(to_actor="human")` to fetch inbox, `sdk.messages.create(...)` to post.

## Implementation Notes

- Layout: master-detail like the drafts tab. Message list on left, content on right.
- Messages ordered by `created_at` descending (newest first).
- Fetch via `sdk.messages.list(to_actor="human")` in the data layer.
- Action buttons: parse from `content` JSON. When clicked, post a new message (e.g. `action_command`) back to the server.
- Reuse patterns from `packages/dashboard/tabs/drafts.py` (ListItem subclass, fixed action bar, Markdown content pane).
- After inbox tab works: remove `orchestrator/message_utils.py`, update all callers to use `sdk.messages.create()`.

## Possible Next Steps

- Build the inbox tab in the dashboard
- Migrate callers of `message_utils.create_message()` to `sdk.messages.create()`
- Remove `orchestrator/message_utils.py` and local file-based message storage
