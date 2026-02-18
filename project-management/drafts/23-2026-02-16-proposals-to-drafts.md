---
**Processed:** 2026-02-18
**Mode:** human-guided
**Actions taken:**
- Enqueued Task 1 (delete proposal system) as TASK-58ae05c9
- Tasks 2-4 (directory reorg, simplifier agent, rewrite proposal-status) deferred
**Outstanding items:** Tasks 2-4 deferred. Simplifier agent is a future nice-to-have.
---

# Replace Proposals with Drafts + Simplification Agent

**Status:** Partial
**Captured:** 2026-02-16

## Raw

> Create a new agent that submits proposals for cutting line count and simplifying abstractions. Roll the old proposal system into drafts. Separate human vs agent drafts in the directory structure. Add a "proposed" status for agent drafts, "archived" for rejected ones.

## Context

The proposal system (`proposal_utils.py`, `roles/proposer.py`, `roles/curator.py`) is functionally dead — no agents are configured to run as proposer or curator. But the concept is valuable: agents autonomously suggesting work for human review.

Meanwhile, the drafts system already exists with a server API, SDK support, and markdown files. It just needs to absorb the proposal lifecycle and get a new agent that uses it.

## What to Do

### 1. Draft statuses

Current statuses: `idea`, `in_progress`. Add:

| Status | Meaning | Who creates |
|--------|---------|------------|
| `idea` | Human idea, rough | Human |
| `proposed` | Agent-generated suggestion, awaiting human review | Agent |
| `in_progress` | Being worked on (linked to tasks) | Either |
| `accepted` | Human approved, will become tasks | Human |
| `archived` | Rejected or superseded, kept for reference | Human |

No server changes needed — status is a free-text field. Just convention.

### 2. Directory structure

```
project-management/drafts/
  human/          ← human-authored drafts (current drafts move here)
  agent/          ← agent-proposed drafts
```

Existing drafts (1-22) move to `human/`. New agent proposals go to `agent/`. The server `file_path` field tracks where each draft lives. The numbering comes from the server-assigned ID, not the directory.

### 3. Delete old proposal system

Delete these files entirely:
- `orchestrator/proposal_utils.py` (488 lines)
- `orchestrator/roles/proposer.py` (205 lines)
- `orchestrator/roles/curator.py` (271 lines)
- `orchestrator/roles/specialist.py` (if only used by proposer)
- `.claude/commands/proposal-status.md`

Clean up references:
- `orchestrator/config.py` — remove `get_proposals_dir()`, `get_proposal_limits()`, `get_curator_scoring()`, `get_voice_weight()`, `ModelType` literal (or simplify to just `"task"`)
- `orchestrator/reports.py` — remove `list_proposals` import and proposal status section
- `orchestrator/scheduler.py` — remove proposer/curator prompt templates and focus passing
- `tests/test_reports.py` — remove proposal-related test mocks

Estimated deletion: ~960 lines of proposal code + config/test cleanup.

### 4. Simplification agent

A new script-based agent that periodically scans the codebase and proposes simplifications as drafts. Runs infrequently (e.g. once a day or on-demand).

**What it does:**
1. Scans for large files, dead code, over-abstracted patterns
2. Creates drafts via the server API with status `proposed`
3. Writes markdown files to `project-management/drafts/agent/`
4. Each draft includes: what to simplify, estimated line savings, risk level

**What it looks for:**
- Files over 500 lines — suggest splits or deletions
- Functions/classes with zero callers — suggest deletion
- Modules imported by only one file — suggest inlining
- Duplicate logic across files — suggest consolidation
- Test files that test deleted functionality — suggest cleanup
- Config options that are never read — suggest removal

**Agent definition** in `agents.yaml`:
```yaml
- name: simplifier
  role: simplifier
  interval_seconds: 86400  # once per day
  model: haiku  # cheap model for scanning
  paused: true  # start paused, enable when ready
```

**Scripts:** `scripts/simplifier/` with:
- `prompt.md` — instructions for codebase analysis
- `run.sh` — entrypoint
- Uses SDK to create drafts: `sdk.drafts.create(title=..., status='proposed', author='simplifier')`

**Draft format for agent proposals:**
```markdown
# <Title>

**Status:** Proposed
**Author:** simplifier
**Captured:** <date>

## Finding

<What the agent found — e.g. "orchestrator/proposal_utils.py has 0 callers from agents.yaml">

## Suggestion

<What to do about it — e.g. "Delete the file and its test. ~488 lines saved.">

## Estimated Impact

- Lines removed: ~488
- Files affected: 2
- Risk: Low (no callers)

## Evidence

<grep output, import traces, call graphs>
```

### 5. Human review workflow

1. Human runs `/proposal-status` (rewritten to show `proposed` drafts)
2. Reviews agent suggestions
3. Accepts → status changes to `accepted`, optionally creates tasks via `/enqueue`
4. Rejects → status changes to `archived`

Or just: human checks `project-management/drafts/agent/` periodically.

### 6. Rewrite `/proposal-status` command

Change from reading file-based proposals to querying the drafts API:
```python
drafts = sdk.drafts.list(status='proposed')
```

Show agent-proposed drafts awaiting review.

## Implementation Plan

### Task 1: Delete proposal system + clean config
- Delete proposal_utils.py, roles/proposer.py, roles/curator.py, specialist.py
- Clean config.py, reports.py, scheduler.py, tests
- ~960 lines deleted

### Task 2: Organize draft directories + migrate existing
- Create `human/` and `agent/` subdirs
- Move existing 22 drafts to `human/`
- Update server file_path records

### Task 3: Build simplification agent
- Agent definition in agents.yaml
- Scripts in scripts/simplifier/
- Draft creation via SDK
- Test with a manual run

### Task 4: Rewrite /proposal-status to show agent drafts
- Query drafts API for status=proposed
- Show pending agent suggestions

## Open Questions

- Should the simplifier agent also look at test coverage gaps, or keep it focused on line reduction?
- How aggressive should it be? Only suggest deletions of clearly dead code, or also suggest refactors of living code?
- Should agent drafts auto-expire after N days if not reviewed?
- Do we want a `/review-proposals` skill that walks through each one interactively?
