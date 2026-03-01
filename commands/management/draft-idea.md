# /draft-idea - Capture an Idea as a Draft

Capture a rough idea, observation, or suggestion as a draft document for later consideration.

**Argument:** A topic name and/or description of the idea (e.g. `agent progress tracking - we should log where agents spend their turns`)

## Steps

### 1. Parse the input

Extract:
- **Topic** — a short slug for the filename (e.g. `agent-progress-tracking`)
- **Title** — a human-readable title (e.g. "Agent Progress Tracking")
- **Idea** — the user's description, however rough

### 2. Check for related drafts

Use qmd MCP tools to search for existing drafts that cover similar ground. Run both a keyword search and a semantic search:

- `mcp__qmd__search` with key phrases from the idea, `collection: "drafts"`, `limit: 5`
- `mcp__qmd__vector_search` with the idea description, `collection: "drafts"`, `limit: 5`

Review the top results. If any are clearly duplicates or closely related:
- Tell the user which draft(s) already cover this idea (include draft number and title)
- Ask whether to: update the existing draft with the new details, or create a new one anyway
- Do **not** create a file until the user confirms

If no strong matches, proceed.

### 3. Register draft via SDK

Register the draft on the server. The server auto-assigns the next integer ID.

```python
from octopoid.queue_utils import get_sdk
sdk = get_sdk()
result = sdk.drafts.create(
    title=title,
    author="human",
    status="idea"
)
draft_number = result["id"]  # Server-assigned integer
```

### 4. Write the draft file

Use the server-assigned number to build the filename:

```
project-management/drafts/<number>-<YYYY-MM-DD>-<topic-slug>.md
```

For example: `project-management/drafts/3-2026-02-13-agent-progress-tracking.md`

Content:

```markdown
# <Title>

**Captured:** <date>

## Raw

> <The user's exact words, quoted verbatim>

## Idea

<User's description, cleaned up slightly but preserving their intent>

## Invariants

<What should be true about the system after this work is complete? State as
testable behavioural invariants — not what to build, but what should hold.>

- **<invariant-id>**: <human-readable invariant statement>

If the idea doesn't obviously have invariants (e.g. pure refactoring, tooling),
note that explicitly: "No new invariants — this is a refactoring/tooling change."
If you're unsure what the invariants should be, list candidates and flag them
as open questions.

## Context

<Why this came up — reference the conversation or situation if obvious>

## Open Questions

- <Questions that would need answering before this becomes actionable>

## Possible Next Steps

- <What acting on this might look like — tasks, investigations, design docs>
```

Keep it concise. The point is to park the idea, not design the solution.

### 5. Update draft with file path

After writing the file, update the draft record with the file path:

```python
sdk._request("PATCH", f"/api/v1/drafts/{draft_number}", json={"file_path": file_path})
```

### 6. Update qmd index

Re-index and re-embed so the new draft is searchable immediately:

```bash
qmd update && qmd embed
```

This takes ~2 seconds for a single new file.

### 7. Confirm

Tell the user the file was created (include the path and assigned number) and suggest committing it.
