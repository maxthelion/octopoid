# /process-draft - Process a Draft Into Action

Review a draft's status and determine next steps.

**Argument:** Filename or topic (e.g. `dashboard-redesign` or `gatekeeper-review-system-plan.md`)

## Steps

### 1. Find and read the draft

Look in `project-management/drafts/` for the matching file. Drafts use numbered filenames like `32-2026-02-17-scoped-local-server-for-testing.md`. Match by number, topic slug, or title. Read it fully.

### 2. Check for outstanding work

Scan for:
- Unchecked items or TODOs
- "Future work" or "Next steps" sections
- Open questions that weren't resolved
- Alternatives that were deferred, not rejected

**If running in human-guided mode:** list them and ask whether to:
- Create new drafts for them
- Enqueue them as tasks (via `/enqueue`)
- Ignore them

**If running in automated mode (e.g. draft aging agent):** do NOT enqueue tasks or start work directly. Instead:

1. **Check for unresolved open questions first.** If the draft has an "Open Questions" section with unanswered questions, do NOT propose tasks. Instead, surface the questions in the inbox message for the human to answer. The draft gets marked complete either way (it's been filed), but no work should be proposed until the questions are resolved.

2. **Only if no blocking open questions exist**, write proposed tasks to `project-management/drafts/proposed-tasks/` as markdown files, one per task. Use the format:
   ```markdown
   # Proposed Task: <title>

   **Source draft:** <draft filename>
   **Proposed role:** <implement | orchestrator_impl | review>
   **Proposed priority:** <P0-P2>

   ## Context
   <Why this task exists — reference the source draft>

   ## Acceptance Criteria
   - [ ] <criteria>
   ```
3. If multiple related tasks form a coherent project, also write a proposed project file linking them.
4. Send a summary to the human inbox listing what was found, any open questions that need answers, and any proposed tasks.
5. **Do not call `create_task()` or `/enqueue`.** A human (or the PM session) decides what to enqueue.

### 3. Extract rules, patterns, and architecture

Look for content that encodes lasting decisions, constraints, or system design — things future development should follow. Three categories:

#### Rules and patterns
- **Architectural rules** — "X should always go through Y", "never do Z directly"
- **Testing patterns** — "test this kind of feature by doing X"
- **Process rules** — "when approving orchestrator tasks, do X first"
- **Naming conventions** — "branches for X should be named Y"
- **Dependency constraints** — "A must happen before B"

#### Architecture documentation
Look for content that describes **how a subsystem works** — not just rules to follow, but explanations that agents need to understand to work effectively. Signs of architecture content:
- Describes a data flow or control flow (e.g. "the scheduler reads the flow, finds the transition, runs the steps")
- Explains the interaction between multiple components
- Documents a design decision and its rationale (e.g. "agents are pure functions because...")
- Describes a protocol or contract between parts of the system

If the draft contains architecture-level content, check whether an existing doc in `docs/` already covers it. If so, update that doc. If not, create a new one and reference it from `CLAUDE.md` so agents read it.

**If running in human-guided mode:** present the extracted rules and architecture points, and ask which to add to:
- `.claude/rules/` — for rules agents should follow
- `CLAUDE.md` — for project-wide architectural constraints
- `CLAUDE.local.md` — for interactive session workflow
- `docs/` — for architecture documentation and reference (add a `CLAUDE.md` reference so agents find it)

**If running in automated mode:** include proposed rules and architecture docs in the inbox message. Do not modify rule files or docs directly — flag them for human review.

### 4. Decide whether the draft is complete

**Complete if:**
- All proposed work is done (tasks done, changes merged, decisions implemented)
- No outstanding work remains to be scheduled
- The draft served its purpose and is now historical reference

**Still active if:**
- Work has been started but not finished (tasks enqueued but not complete)
- Multi-phase plan with later phases not yet started
- Still actively being referenced for ongoing work
- Open questions remain unanswered

**Update the status field in the markdown frontmatter:**
- If complete: `**Status:** Complete` or `**Status:** Superseded`
- If still active: `**Status:** In Progress` or `**Status:** Partial`

**IMPORTANT:** Do NOT move, rename, or delete draft files. Do NOT create an archive/ directory. Drafts always stay in `project-management/drafts/` regardless of status. The server is the source of truth for draft status — the local file is just a cache.

### 5. Add processing summary (whether archiving or not)

Prepend a processing summary block to track what's been done:

```markdown
---
**Processed:** <date>
**Mode:** <human-guided | automated | mixed>
**Actions taken:**
- <brief description of each action, e.g. "Enqueued as TASK-xxx", "Extracted rule to .claude/rules/foo.md">
- <...>
**Outstanding items:** <none | list of items still to do, or "keeping in drafts/">
---
```

**Mode definitions:**
- `human-guided` — human reviewed each step and made decisions (the normal `/process-draft` flow)
- `automated` — processed by an agent without human intervention (e.g. post-accept hook)
- `mixed` — some steps automated, some required human input

### 6. Update status on server

Update the draft status via SDK:

```python
from orchestrator.queue_utils import get_sdk
sdk = get_sdk()
sdk._request("PATCH", f"/api/v1/drafts/{draft_id}", json={"status": new_status})
```

Where `new_status` is `"complete"`, `"superseded"`, `"in_progress"`, or `"partial"` based on the decision in step 4.

The local file in `project-management/drafts/` is just a cache — the server is the source of truth.
