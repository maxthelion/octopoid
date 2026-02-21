# Codebase Analyst Agent

**Status:** Idea
**Captured:** 2026-02-21

## Raw

> A daily agent that runs a script to find the largest/most complex files in the codebase, then drafts a proposal for simplification. It creates actions (e.g. "Enqueue refactor", "Dismiss") and sends a message to the user inbox. It has a guard script that runs first — if there are already unresolved drafts from this agent, the script returns "skip" and the agent exits without doing work. Since it only runs daily, the cost of spawning just to check the guard is acceptable. The guard script checks for existing drafts with author=codebase-analyst and status=idea via the SDK. The agent itself uses the find-large-files script output to identify candidates, writes a draft via sdk.drafts.create, attaches actions via sdk.actions.create with action_data JSON, and posts to the user inbox via sdk.messages.create.

## Idea

A background agent that runs once daily to scan the codebase for files that have grown too large or complex, and proposes simplification work.

### Flow

1. **Guard script runs first** — checks the server for existing drafts where `author=codebase-analyst` and `status=idea`. If any exist, outputs `SKIP` and the agent exits immediately. No wasted turns. Since the agent only runs daily, the spawn-to-check cost is acceptable.

2. **Analysis script runs** — finds the largest files in the codebase (by line count, function count, or cyclomatic complexity). Outputs a structured report of candidates.

3. **Agent reads the report** and picks the top candidate. It:
   - Creates a draft via `sdk.drafts.create(title=..., author="codebase-analyst", status="idea")` explaining what's wrong and how it could be simplified
   - Attaches actions via `sdk.actions.create()` with `action_data` JSON:
     ```json
     {
       "buttons": [
         {"label": "Enqueue refactor", "command": "Create a task to refactor <file>. Split into <modules>. Priority P2, role implement."},
         {"label": "Dismiss", "command": "Set draft <N> status to superseded via the SDK. The file is fine as-is."}
       ]
     }
     ```
   - Posts a message to the user inbox via `sdk.messages.create(to_actor="human", type="action_proposal", ...)` so it shows up in the dashboard

### Agent config

```yaml
# .octopoid/agents/codebase-analyst/agent.yaml
role: analyse
model: sonnet
max_turns: 30
interval_seconds: 86400  # daily
spawn_mode: scripts
lightweight: true
allowed_tools:
  - Read
  - Glob
  - Grep
  - Bash
```

### Scripts

- `scripts/guard.sh` — checks for existing unresolved drafts from this agent
- `scripts/find-large-files.sh` — finds largest/most complex files, outputs structured report

### Guard pattern

This is the first agent to use a guard script. The pattern is: the agent spawns, runs the guard script as its first action, and exits early if the guard says no work is needed. This is a general pattern other agents could reuse — e.g. a draft curator that skips if no new drafts exist, or a test runner that skips if no code has changed.

## Context

Came up while discussing what kinds of agents could use the Draft 68 action/inbox system. The codebase analyst is a good first consumer — it proposes work, the user approves or dismisses via action buttons, and the inbox processor spawns a worker to execute. It exercises the full action → inbox → worker pipeline.

Also motivated by practical need: several files in the orchestrator have grown large (scheduler.py, reports.py) and would benefit from periodic review.

## Dependencies

- Draft 68 (actions as agent instructions) — the action_data JSON field and inbox processor
- Messages table — already live on server
- Actions table — needs action_data column (server task exists)

## Open Questions

- What metrics should the analysis script use? Line count is simple but crude. Function count or complexity metrics might be better but need tooling.
- Should the agent propose one file per run, or batch multiple candidates into a single draft?
- What's the threshold for "too large"? Configurable? Hardcoded?
- Should the guard check for pending actions too (not just idea-status drafts)?
- Could this agent also check for other code smells (dead imports, duplicate code, missing tests)?

## Possible Next Steps

- Write the guard script (Python one-liner checking `sdk.drafts.list(author="codebase-analyst", status="idea")`)
- Write the find-large-files script (line count per file, sorted descending)
- Create the agent config and prompt
- Test the full flow once Draft 68's inbox processor is working
