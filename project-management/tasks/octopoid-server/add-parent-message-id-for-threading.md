# Add parent_message_id to messages table for threading

**Priority:** P2
**Related project:** PROJ-7485e8a2 (Inbox UX overhaul)

## Context

The inbox needs threaded conversations so follow-up responses reference the full chain. For example, when a human asks to process a draft, the action agent might respond with "this draft has open questions — want to answer them?", and the human's reply should be linked to that thread.

## Schema change

Add `parent_message_id` column to the messages table:

```sql
ALTER TABLE messages ADD COLUMN parent_message_id TEXT REFERENCES messages(id);
CREATE INDEX idx_messages_parent ON messages(parent_message_id);
```

If the messages table doesn't exist yet (i.e. add-messages-table.md hasn't been implemented), include `parent_message_id` in the initial CREATE TABLE instead.

## API changes

- `POST /api/v1/messages` — accept optional `parent_message_id` field
- `GET /api/v1/messages` — accept optional `thread_id` query param that returns all messages in a thread (the root message and all descendants)
- Include `parent_message_id` in all message response objects

## SDK changes

- `sdk.messages.create()` — accept optional `parent_message_id` parameter
- `sdk.messages.list()` — accept optional `thread_id` parameter

## Acceptance Criteria

- [ ] Messages table has `parent_message_id` column (nullable, references messages.id)
- [ ] POST /api/v1/messages accepts and stores parent_message_id
- [ ] GET /api/v1/messages supports thread_id query param
- [ ] parent_message_id included in message response objects
- [ ] SDK create() and list() support the new parameters
