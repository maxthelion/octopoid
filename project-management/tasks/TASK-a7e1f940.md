# [TASK-a7e1f940] Init script: set up project-management/commands and install draft-idea

ROLE: implement
PRIORITY: P2
BRANCH: feature/client-server-architecture
CREATED: 2026-02-13T00:00:00Z
CREATED_BY: human
EXPEDITE: false
SKIP_PR: true

## Context

Currently the init script (`orchestrator/init.py`) installs management commands (enqueue, queue-status, etc.) from `commands/management/` into `.claude/commands/`. The `draft-idea` command lives only in `.claude/commands/` and isn't part of the init flow at all.

We want to:
1. Have the init script create a `project-management/commands/` directory
2. Copy the `draft-idea` command into `project-management/commands/` on init
3. Place a launcher script in `.octopoid/` on init so the command can be invoked
4. Eventually move all commands from `.claude/commands/` into `project-management/commands/`, but make that an optional migration step for existing users

## Changes Required

### 1. Move command source files
- Move `draft-idea.md` (and any other project-management commands) into `commands/management/` (or a new `commands/project/` directory — use judgement)
- These become the source templates that init copies from

### 2. Update init script (`orchestrator/init.py`)
- Add `project-management/commands/` to the directories created during init (line ~96-104)
- Copy `draft-idea.md` into `project-management/commands/` during init
- Create a launcher script in `.octopoid/` that makes the command accessible (e.g. `.octopoid/scripts/draft-idea.sh` or similar)
- This should be part of the existing skills installation step (or a new optional step)

### 3. Migration path for existing users
- Existing users already have commands in `.claude/commands/`
- Don't break their setup — the migration from `.claude/commands/` to `project-management/commands/` should be optional
- Consider adding a `--migrate-commands` flag or an interactive prompt during init that offers to move them
- If commands exist in both locations, prefer `project-management/commands/`

### 4. Update documentation
- Update init script's "Next steps" output to mention the new command location
- Update any references to `.claude/commands/` that should now point to `project-management/commands/`

## Acceptance Criteria

- [ ] Init creates `project-management/commands/` directory
- [ ] Init copies `draft-idea` command into `project-management/commands/`
- [ ] Init places a script in `.octopoid/` for invoking the command
- [ ] Existing `.claude/commands/` setups continue to work
- [ ] Optional migration path exists for moving commands to new location
- [ ] Init output reflects the new directory structure
