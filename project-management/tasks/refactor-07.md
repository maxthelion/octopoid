# refactor-07: Create implementer agent directory template

ROLE: implement
PRIORITY: P2
BRANCH: feature/client-server-architecture
CREATED: 2026-02-15T00:00:00Z
CREATED_BY: human
SKIP_PR: true

## Context

Agent configuration is currently scattered across the codebase:
- Agent type config: `.octopoid/agents.yaml` (model, interval, max_turns)
- Prompt template: `orchestrator/prompts/implementer.md` ($variable substitution)
- Implementation guidelines: `commands/agent/implement.md`
- Scripts: `orchestrator/agent_scripts/` (finish, fail, submit-pr, run-tests, record-progress)

The agent directories proposal (`project-management/drafts/9-2026-02-15-agent-directories.md`) consolidates everything about an agent type into a single directory. This task creates the implementer template directory.

This is a PRODUCT template -- it ships with octopoid in `packages/client/agents/` and gets scaffolded into user projects via `octopoid init`. It must be generic (no repo-specific references).

Reference: `project-management/drafts/9-2026-02-15-agent-directories.md`

## What to do

Create the directory `packages/client/agents/implementer/` with the following files:

### 1. `agent.yaml`

Agent type configuration (defaults that can be overridden by fleet config):

```yaml
role: implementer
model: sonnet
max_turns: 200
interval_seconds: 60
spawn_mode: scripts
lightweight: false
allowed_tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - Skill
```

### 2. `prompt.md`

Move the content from `orchestrator/prompts/implementer.md`. This is the prompt template with `$variable` substitution that gets rendered and passed to `claude -p`.

Copy the file contents as-is. Keep all template variables (`$task_id`, `$task_title`, `$task_content`, `$global_instructions`, `$scripts_dir`, `$review_section`, `$continuation_section`, `$required_steps`, etc.). The renderer will substitute these at spawn time.

Do NOT modify the template variables or format. The exact same rendering code (`orchestrator/prompt_renderer.py`) will be used.

### 3. `instructions.md`

Move the content from `commands/agent/implement.md`. This is the implementation guidelines document that gets appended to the prompt or injected as context.

Copy the file contents as-is. This is generic guidance (code quality, git workflow, error handling) -- it should already be repo-agnostic.

### 4. `scripts/` directory

Copy the following scripts from `orchestrator/agent_scripts/`:

- `submit-pr` -- Push branch and create PR
- `run-tests` -- Detect and run test suite
- `finish` -- Mark task complete
- `fail` -- Mark task failed
- `record-progress` -- Save progress note

Copy each script as-is. They use `$TASK_ID`, `$WORKTREE`, etc. environment variables that are set by `env.sh` at spawn time.

Make sure all scripts are executable (`chmod 755`).

### Directory structure

```
packages/client/agents/
  implementer/
    agent.yaml
    prompt.md
    instructions.md
    scripts/
      submit-pr
      run-tests
      finish
      fail
      record-progress
```

### Important notes

- Do NOT delete the original files (`orchestrator/prompts/implementer.md`, `commands/agent/implement.md`, `orchestrator/agent_scripts/`). Those are still referenced by the current scheduler. Task refactor-12 handles cleanup.
- Do NOT add repo-specific content. No references to octopoid's own codebase, specific test commands, or internal structure.
- The `scripts/` are bash/python scripts -- make sure line endings are Unix (LF, not CRLF).

## Key files

- `packages/client/agents/implementer/` -- directory to create (NEW)
- `orchestrator/prompts/implementer.md` -- source for prompt.md (line 1, template with $variables)
- `commands/agent/implement.md` -- source for instructions.md
- `orchestrator/agent_scripts/` -- source for scripts/ (5 scripts: submit-pr, run-tests, finish, fail, record-progress)
- `project-management/drafts/9-2026-02-15-agent-directories.md` -- design reference

## Acceptance criteria

- [ ] `packages/client/agents/implementer/` directory exists
- [ ] `agent.yaml` has correct config fields: role, model, max_turns, interval_seconds, spawn_mode, lightweight, allowed_tools
- [ ] `prompt.md` contains the implementer prompt template (same content as `orchestrator/prompts/implementer.md`)
- [ ] `instructions.md` contains implementation guidelines (same content as `commands/agent/implement.md`)
- [ ] `scripts/` directory has all 5 scripts: submit-pr, run-tests, finish, fail, record-progress
- [ ] All scripts are executable
- [ ] Content is generic -- no repo-specific references
- [ ] Original source files are NOT deleted
- [ ] All existing tests pass
