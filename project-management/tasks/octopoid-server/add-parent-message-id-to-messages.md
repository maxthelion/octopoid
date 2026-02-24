# Add parent_message_id to messages table for threading support

## Context

Octopoid's inbox now supports message threading so follow-up conversations
form coherent chains. When an action agent responds and the human replies,
the reply should reference the original message as its parent.

The orchestrator client-side code already sends `parent_message_id` when
creating messages and uses it to reconstruct conversation threads in the
dashboard. However, the server currently ignores and drops this field.

## Changes Required

### 1. Database migration — add `parent_message_id` column

In the D1 database schema (wherever migrations live), add a nullable column
to the messages table:

```sql
ALTER TABLE messages ADD COLUMN parent_message_id TEXT;
```

### 2. Update POST /api/v1/messages handler

In the route handler for `POST /api/v1/messages`:
- Accept `parent_message_id` from the request body (optional, string or null)
- Store it in the database row when present

### 3. Update GET /api/v1/messages handler

Return `parent_message_id` as part of each message object in the response.
It should be `null` when not set.

Optionally, support filtering by `parent_message_id` query parameter to fetch
all replies to a given message:
```
GET /api/v1/messages?parent_message_id=msg-123
```

### 4. Update TypeScript types

In `src/types/shared.ts` (or wherever message types are defined):

```typescript
interface Message {
  id: string
  task_id: string
  from_actor: string
  to_actor?: string
  type: string
  content: string
  created_at: string
  scope?: string
  parent_message_id?: string | null  // add this
}

interface CreateMessageRequest {
  task_id: string
  from_actor: string
  type: string
  content: string
  to_actor?: string
  parent_message_id?: string | null  // add this
}
```

## Acceptance Criteria

- [ ] `parent_message_id` column exists on the messages table
- [ ] POST /api/v1/messages accepts and persists `parent_message_id`
- [ ] GET /api/v1/messages returns `parent_message_id` (null when not set)
- [ ] Filtering by `parent_message_id` works (GET /api/v1/messages?parent_message_id=X)
- [ ] Existing messages without a parent are unaffected (field is null)
- [ ] TypeScript types updated accordingly
