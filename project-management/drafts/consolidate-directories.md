# Plan: Consolidate directories + remove Python agent path

## Context

Octopoid has two overlapping directories (`.orchestrator/` and `.octopoid/`) from different eras, and two agent execution paths (Python module invocation and script-based `claude -p`). The Python path is the active default; the scripts path is complete but never enabled. We're consolidating to one directory (`.octopoid/`) and making scripts mode the only agent path for implementers.

## Phase 0: Stop scheduler, clean slate

1. `launchctl unload ~/Library/LaunchAgents/com.octopoid.scheduler.plist`
2. Remove all git worktrees under `.orchestrator/`: `git worktree list | grep .orchestrator | awk '{print $1}' | xargs -I{} git worktree remove --force {}`
3. Delete both `.orchestrator/` and `.octopoid/` entirely

## Phase 1: Update `orchestrator/init.py` — the single source of truth for directory structure

Rewrite init.py to create the new consolidated layout. Key changes:

### New directory layout
```
.octopoid/
├── config.yaml              # Server, hooks, task types (committed)
├── agents.yaml              # Agent definitions (committed)
├── global-instructions.md   # Agent instructions (committed)
├── runtime/                 # All ephemeral state (gitignored)
│   ├── agents/{name}/       # Per-agent: state.json, worktree/, exit_code, logs
│   ├── tasks/{id}/          # Per-task: worktree/, scripts/, task.json, prompt.md, env.sh, result.json
│   ├── shared/              # Notes, reviews, proposals
│   ├── logs/                # Scheduler + agent logs
│   ├── messages/            # Agent-to-human messages
│   └── scheduler.lock
└── tasks/                   # Task markdown content (gitignored, server is source of truth)

project-management/          # Project planning (committed)
├── drafts/                  # Plan drafts
├── projects/                # Active projects
└── tasks/                   # Task specs / descriptions
```

### Init script behavior
- Creates `.octopoid/` and `project-management/` structures
- For each config file (`config.yaml`, `agents.yaml`, `global-instructions.md`): **check if it exists first, default to keeping existing, only write if missing**
- Same for `project-management/` subdirs — create if missing, never overwrite
- Update `EXAMPLE_AGENTS_YAML` to reflect v2 API-mode config (current version references file-based queues, proposal model, etc. — all deprecated)
- Update `GITIGNORE_ADDITIONS` to use `.octopoid/runtime/` patterns instead of `.orchestrator/` patterns
- Update all print messages from `.orchestrator` → `.octopoid`

## Phase 2: Update `orchestrator/config.py` — central path resolution

- `get_orchestrator_dir()` returns `.octopoid/` instead of `.orchestrator/`
- `get_tasks_file_dir()` returns `get_orchestrator_dir() / "tasks"`
- Add `get_runtime_dir()` returning `get_orchestrator_dir() / "runtime"`
- `get_agents_runtime_dir()` returns `get_runtime_dir() / "agents"`
- `get_tasks_dir()` (ephemeral task worktrees) returns `get_runtime_dir() / "tasks"`
- `get_logs_dir()` returns `get_runtime_dir() / "logs"`
- `get_shared_dir()` returns `get_runtime_dir() / "shared"`

## Phase 3: Make scripts mode the only path for implementers

### `orchestrator/scheduler.py`
- Remove `agent_mode` config check — implementers always use scripts path
- When `role == "implementer"` and there's a claimed task: `prepare_task_directory()` → `invoke_claude()`
- Keep `spawn_agent()` for non-implementer roles (gatekeeper, github_issue_monitor, orchestrator_impl)
- Fix `prepare_task_directory()` to use `get_tasks_dir()` (runtime/tasks/) not `get_orchestrator_dir() / "tasks"` (content files)

### Delete `orchestrator/roles/implementer.py` (950 lines)
- No other file imports from it
- All logic replaced by: `prepare_task_directory()`, `invoke_claude()`, `handle_agent_result()`, and agent scripts

### Keep as-is:
- `orchestrator/roles/base.py` — still used by gatekeeper, orchestrator_impl
- `orchestrator/agent_scripts/` — the scripts Claude uses
- `orchestrator/prompt_renderer.py` + `orchestrator/prompts/implementer.md`
- `orchestrator/hook_manager.py`, `orchestrator/hooks.py`

## Phase 4: Update all other references

- `orchestrator/roles/orchestrator_impl.py` — hardcoded venv path
- `orchestrator/com.octopoid.scheduler.plist` — all paths
- `scripts/*.sh` — shell scripts
- `.gitignore` — update patterns
- Tests (~15 files) — mock path strings
- Docs (~3 files) — `.orchestrator` references

## Phase 5: Recreate via init

Run `python orchestrator/init.py -y` to create the new `.octopoid/` structure from scratch. Then copy in our actual config:
- Write `config.yaml` with current server URL + hooks config
- Write `agents.yaml` with current agent definitions (implementer-1, implementer-2, github-issue-monitor)
- Write `global-instructions.md` if we had one

## Phase 6: Restart and verify

1. `launchctl load ~/Library/LaunchAgents/com.octopoid.scheduler.plist`
2. Run unit tests: `pytest tests/test_hooks.py tests/test_hook_manager.py tests/test_repo_manager.py`
3. Watch scheduler logs
4. Verify implementer picks up task in scripts mode

## Files to modify (priority order)

| File | Change |
|------|--------|
| `orchestrator/init.py` | Rewrite: new layout, project-management/, preserve-existing behavior |
| `orchestrator/config.py` | Central path resolution — `.octopoid/`, `runtime/` subdir |
| `orchestrator/scheduler.py` | Scripts-only for implementers, fix task dir paths |
| `orchestrator/roles/implementer.py` | **DELETE** |
| `.gitignore` | Update patterns |
| `orchestrator/com.octopoid.scheduler.plist` | Path updates |
| `orchestrator/roles/orchestrator_impl.py` | Venv path |
| `scripts/*.sh` | Path updates |
| `tests/` (~15 files) | Mock path strings |
| `docs/` (~3 files) | Documentation |
