# [TASK-c8b623bf] Clean up /draft-idea command and add server-side draft numbering

ROLE: implement
PRIORITY: P2
BRANCH: feature/client-server-architecture
CREATED: 2026-02-13T00:00:00Z
CREATED_BY: human
EXPEDITE: false
SKIP_PR: true

## Context

The project has gone through various incarnations and the `/draft-idea` command has vestiges of former versions that need cleaning up. There should be no mention of local databases, non-existent scripts, or subdirectory structures that were never created.

Current issues in `.claude/commands/draft-idea.md`:
1. References `boxen/` and `octopoid/` subdirectories in `project-management/drafts/` — these don't exist and aren't needed
2. References `project-management/scripts/next-draft.sh` — doesn't exist
3. References `project-management/drafts/.counter` — doesn't exist
4. Step 3 classifies ideas as boxen vs octopoid — not relevant
5. Step 6 uses `orchestrator.db` (old local DB code) instead of the SDK

## Changes Required

### 1. Server (`submodules/server`)
- Update drafts table schema: use auto-incrementing integer ID instead of client-supplied string ID
- Update `POST /api/v1/drafts` to auto-assign the next number (client should NOT supply an ID)
- Return the assigned ID in the response

### 2. Python SDK (`packages/python-sdk/octopoid_sdk/client.py`)
- Add `create()` method to `DraftsAPI` class (currently only has `list()`)
- `create()` should POST to `/api/v1/drafts` with title, author, file_path, status
- Should NOT send an ID — server auto-assigns it

### 3. Command file (`.claude/commands/draft-idea.md`)
- Remove step 3 (classify as boxen/octopoid)
- Remove subdirectory references — drafts go flat in `project-management/drafts/`
- Remove `next-draft.sh` script reference
- Remove `.counter` file reference
- Remove `orchestrator.db` code in step 6
- New flow: parse input → check duplicates via `sdk.drafts.list()` → register via `sdk.drafts.create()` (server returns auto-assigned number) → write markdown to `project-management/drafts/<number>-<date>-<slug>.md` → confirm

## Acceptance Criteria

- [ ] Server drafts table uses auto-incrementing integer IDs
- [ ] POST /api/v1/drafts auto-assigns next number, does not require client ID
- [ ] SDK DraftsAPI has create() method
- [ ] /draft-idea command has no subdirectory references
- [ ] /draft-idea command has no script or counter file references
- [ ] /draft-idea command has no orchestrator.db or local DB references
- [ ] /draft-idea uses SDK to register drafts and get assigned number
- [ ] Drafts written flat to project-management/drafts/<number>-<date>-<slug>.md
