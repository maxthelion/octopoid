# Changelog

All notable changes to Octopoid will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Lease expiry housekeeping job** ([TASK-96a53880])
  - Added `check_and_requeue_expired_leases()` to `orchestrator/scheduler.py` as an orchestrator-side fallback for tasks stuck in "claimed" when the server's lease-monitor doesn't run.
  - On each scheduler tick, lists all claimed tasks, checks `lease_expires_at`, and moves expired tasks back to the "incoming" queue with `claimed_by=None` and `lease_expires_at=None`.
  - Logs each requeue via `debug_log` and `print` for visibility.
  - Registered in `HOUSEKEEPING_JOBS` so it runs automatically on every tick.

### Changed

- **Gatekeeper rejections now include explicit rebase instructions** ([TASK-12056c21])
  - Updated gatekeeper `instructions.md` and `prompt.md` (both runtime `.octopoid/` and template `packages/client/`) to require a "Before Retrying" section with `git fetch origin && git rebase origin/<base_branch>` in every rejection comment
  - `reject_with_feedback` step in `orchestrator/steps.py` now posts the review comment to the PR on rejection (previously only done on approval), and automatically appends rebase instructions to the rejection reason if none are already present

- **Pool model step 1: convert `fleet:` list to `agents:` dict format** ([TASK-861f0682])
  - `.octopoid/agents.yaml` now uses an `agents:` dict where each key is a blueprint name; `max_instances` controls how many concurrent instances are allowed
  - `get_agents()` in `orchestrator/config.py` reads the new dict format and injects `blueprint_name` and `max_instances` (default 1) into each returned entry
  - Full backwards compatibility: if `agents:` is not a dict, falls back to reading the legacy `fleet:` list
  - `EXAMPLE_AGENTS_YAML` template in `orchestrator/init.py` updated to use the new dict format
  - 15 unit tests added in `tests/test_config_agents.py`

- **Scheduler cleanup: remove dead code, flatten `handle_agent_result`** ([TASK-33d1f310])
  - Removed unused `agent_role` key from `spawn_implementer` state.extra (replaced by `claim_from` in the role-name refactor)
  - Extracted result parsing from `handle_agent_result` into `_read_or_infer_result(task_dir)` — handles file existence, JSON validity, and notes.md fallback
  - Extracted outcome handlers into `_handle_done_outcome`, `_handle_fail_outcome`, and `_handle_continuation_outcome` helpers
  - `handle_agent_result` is now flat (2 levels of nesting) — one `try` block with an `elif` chain dispatching to helpers

### Fixed

- **Fix orphan bugs: `guard_not_running` state corruption + exit code tracking** ([TASK-ccf602a6])
  - `guard_not_running` no longer calls `mark_finished` when `state.pid` is `None`. Previously, a `running=True` state with no PID (spawn failed before state was written) triggered an unconditional crash-mark, corrupting `consecutive_failures` and `total_failures`. Now: if `running=True` with a dead PID, it marks crashed; if `running=True` with no PID, it clears the flag without counting as a failure.
  - `check_and_update_finished_agents` now reads `result.json` when no `exit_code` file is present. Pure-function agents (spawned via `invoke_claude`) never write an `exit_code` file, so the old code always defaulted to `exit_code=1` (crash). Now: when `exit_code` file is absent, the agent's `result.json` is consulted via `_read_or_infer_result`; outcomes `"done"` and `"submitted"` map to exit code 0 (success), all others to 1.

- **Worktree detached HEAD enforcement** ([TASK-6ee319d0])
  - `prepare_task_directory` in `scheduler.py` no longer calls `repo.ensure_on_branch()` after creating the worktree. Worktrees must always stay on detached HEAD; agents create a named branch only when ready to push via `create_task_branch`.
  - Removed the now-unused `RepoManager` import from `scheduler.py`.
  - Fixed `get_main_branch()` calls in `scheduler.py` and `git_utils.py` — the function was never defined (NameError at runtime); replaced with the correct `get_base_branch()` from `orchestrator.config`.
  - Added a safety assertion at the end of `create_task_worktree` that verifies the returned worktree is on detached HEAD (`git rev-parse --abbrev-ref HEAD == "HEAD"`). This catches future regressions where the pipeline accidentally checks out a named branch.
  - Project tasks (with `project_id`) can now be spawned even when the project branch is already checked out in the main working tree — previously this caused a `git exit code 128` crash.

- **`merge_pr` step now raises on failure** ([TASK-37b6e117])
  - `merge_pr` in `orchestrator/steps.py` now checks the return value of `approve_and_merge()` and raises `RuntimeError` when the result contains an `"error"` key
  - Previously, a failed merge (e.g. due to merge conflicts) was silently swallowed, leaving the task in `provisional` queue and causing the gatekeeper to re-claim and re-approve it in an infinite loop
  - Added `guard_pr_mergeable` guard to the gatekeeper's AGENT_GUARDS chain (runs after `guard_claim_task`)
  - The guard calls `gh pr view --json mergeable` to check the PR's merge status before spawning; if the PR is `CONFLICTING`, it releases the claim, rejects the task back to `incoming` with rebase instructions, and blocks the spawn

- **`create_pr` step: use task branch as PR base, not hardcoded main** ([TASK-e37bc845])
  - `create_pr` now passes `base_branch=task.get("branch", "main")` to `RepoManager` instead of defaulting to `"main"`
  - PRs created for tasks with a custom branch (e.g. `feature/client-server-architecture`) now target the correct base, preventing spurious CONFLICTING status on GitHub

- **`run_tests` flow step PATH for pnpm under launchd** ([TASK-5eb215f6])
  - `run_tests` now builds an augmented PATH before calling the test subprocess, adding nvm's active node bin directory and corepack shims directories so `npm`/`pnpm` are found even when the scheduler runs under launchd with a minimal environment
  - Introduced `_build_node_path()` helper that inspects `NVM_DIR` and well-known corepack shim locations

- **Flow dispatch error recovery** ([TASK-31b1fe65])
  - When `handle_agent_result_via_flow` throws an exception, the task is now moved to `failed` queue instead of being left in `claimed` forever
  - Error details (including full traceback) are logged via `debug_log`
  - The exception message is recorded in the task's `execution_notes` field
  - If moving to `failed` also fails, that secondary error is caught and logged rather than propagated

- **Gatekeeper claim bug** ([TASK-b0a63d8b])
  - `guard_claim_task` now passes `role_filter=None` when claiming from non-incoming queues (e.g. `provisional`), so the gatekeeper can claim tasks whose original `role` is `"implement"` rather than `"gatekeeper"`
  - Added `role_filter` parameter to `claim_and_prepare_task` with a sentinel default so callers can explicitly pass `None` to disable role filtering

### Added

- **Dashboard: async background data loading for snappy tab switching** ([TASK-33a56b51])
  - Data is now fetched in a background thread (`_data_loop`) instead of blocking the main loop
  - Main input loop uses a 100ms `getch` timeout — tab switching and cursor navigation are instant
  - `r`/`R` signals the background thread to refresh immediately (no blocking)
  - Lock (`_data_lock`) protects shared state between the render and data threads
  - Added tests for the new background thread behavior and `r`/`R` key signalling

- **Integration test: mini 2-task project lifecycle** ([TASK-1597e6f5])
  - Added `tests/test_project_lifecycle.py` with 3 tests verifying the full project lifecycle
  - Tests use a local bare repo as "origin" — no external dependencies required
  - Verifies: worktrees created as detached HEADs from the project branch, task 1 commits visible in task 2 worktree, cleanup preserves worktree with detached HEAD
  - Matches the current `create_task_worktree` / `cleanup_task_worktree` API (detached HEAD model)

- **Drafts tab in dashboard** ([TASK-451ec77d])
  - Added `TAB_DRAFTS = 5` constant and updated `TAB_NAMES`/`TAB_KEYS` to include "Drafts" / "F"
  - New `render_drafts_tab()` with master-detail layout: left pane lists drafts (number + title), right pane shows full markdown content
  - `DashboardState` gains `drafts_cursor` and `drafts_content` fields
  - `_load_draft_content()` reads the selected draft's markdown file from `project-management/drafts/`
  - j/k navigation moves between drafts; content is loaded on selection change
  - Tab accessible via `F`, `f`, or `6` keys
  - 16 new tests covering rendering (including empty list), tab switching, and cursor navigation

- **Implementer as pure-function via flow steps** ([TASK-2bf1ad9b])
  - Registered `push_branch`, `run_tests`, `create_pr`, `submit_to_server` steps in `orchestrator/steps.py`
  - Implementer lifecycle (`claimed → provisional`) is now fully flow-driven — no hardcoded handler
  - Scheduler dispatches implementer results via `handle_agent_result_via_flow()` (same path as gatekeeper)
  - Implementer prompt updated: agent writes `result.json` (`{"status": "success"}` or `{"status": "failure", ...}`) instead of calling `submit-pr` / `finish` / `fail`
  - Removed `submit-pr`, `finish`, and `fail` scripts from implementer agent; kept `run-tests` and `record-progress`
  - Updated `global-instructions.md` to reflect pure-function model (no direct git push / PR creation)
  - Added `tests/test_steps.py` with 8 unit tests for the step registry and implementer steps
  - Added `TestImplementerFlow` class in integration tests covering success, failure, step registration, and flow YAML validation

- **Flow-driven scheduler execution** ([TASK-f584b935])
  - Added `orchestrator/steps.py` with `STEP_REGISTRY`, `execute_steps()`, and gatekeeper steps (`post_review_comment`, `merge_pr`, `reject_with_feedback`)
  - Added `.octopoid/flows/default.yaml` declaring the standard implementation flow with gatekeeper on the `provisional → done` transition
  - Added `handle_agent_result_via_flow()` in scheduler replacing the hardcoded `if agent_role == "gatekeeper"` dispatch
  - Added `get_claim_queue_for_role()` to derive claim queue from flow definition rather than agent config
  - Updated `guard_claim_task()` to use flow-driven claim queue lookup
  - Updated `generate_default_flow()` to match new gatekeeper-based flow structure
  - Added `test_flow_driven_gatekeeper_claim_queue` integration test
  - Future agent types register steps and add flow YAML without modifying the scheduler

- **Flow tests for task lifecycle using scoped SDK** ([TASK-848f426f])
  - Added `tests/integration/test_flow.py` with 11 tests covering full state machine paths
  - Tests: claim with role filter, claim with type filter, requeue to incoming, double claim fails, submit without claim fails, reject preserves metadata, blocked task not claimable, unblock on accept, scope claim isolation, full happy path, reject returns to incoming
  - Added `tests/integration/flow_helpers.py` with reusable `create_task`, `create_and_claim`, `create_provisional` helpers
  - All tests use `scoped_sdk` fixture for complete per-test isolation
  - All tests skip gracefully when local server is not running

### Fixed

- **Guard flow dispatch against unknown decision values** ([TASK-46eb663d])
  - `handle_agent_result_via_flow()` now only executes `transition.runs` (including `merge_pr`) on an explicit `decision == "approve"`
  - Unknown or missing `decision` values (e.g. `None`, `"banana"`) log a warning and return without action, leaving the task in its current queue for human review
  - Added 5 unit tests in `tests/test_scheduler_refactor.py` covering approve, reject, `None`, and unknown decision cases

- **Detect and fix worktree branch mismatches in scheduler** ([TASK-a4d02a1c])
  - `create_task_worktree()` now checks if an existing worktree is based on the correct branch before reusing it
  - If `origin/<branch>` is not an ancestor of the worktree HEAD, the worktree is deleted and recreated from the correct branch
  - Branch mismatch is logged clearly via `print()` for debug visibility
  - Added `_worktree_branch_matches()` helper that uses `git merge-base --is-ancestor`
  - Treats missing remote branches as a match to avoid spurious deletions
  - Added 5 new unit tests covering match, mismatch, logging, new worktree, and missing origin cases

- **Simplify create_task_worktree: remove dead ancestry logic** ([TASK-3288d983])
  - Removed obsolete branch ancestry checking (ls-remote, merge-base --is-ancestor)
  - Removed remote and local branch deletion logic (git push origin --delete, git branch -D)
  - Reduced create_task_worktree() from ~60 lines to ~20 lines
  - Worktrees now always created as detached HEADs from the correct base branch
  - Tests updated to reflect simplified implementation

- **Fix worktree creation: detached HEADs + branch lifecycle** ([TASK-8f741bbf])
  - Worktrees now always created as detached HEADs (no branch conflicts)
  - Added `_add_detached_worktree()` and `_remove_worktree()` helper functions
  - Added `RepoManager.ensure_on_branch()` to create branch from detached HEAD
  - Updated `push_branch()` to raise clear error on detached HEAD
  - Updated `create_pr()` to accept `task_branch` param and handle detached HEAD
  - Added `TASK_BRANCH` env var to scheduler's env.sh
  - Updated submit-pr script to pass TASK_BRANCH to create_pr
  - Fixed `cleanup_task_worktree()` to handle detached HEAD gracefully
  - Resolves issue where agents couldn't start work due to "branch already exists" errors

### Refactored

- **Refactor queue_utils.py into entity modules** ([TASK-7a393cef])
  - Split 2,711-line `queue_utils.py` into 7 focused modules:
    - `sdk.py`: SDK initialization and orchestrator ID (112 lines)
    - `tasks.py`: Task lifecycle, CRUD, and query operations (664 lines)
    - `projects.py`: Project management (322 lines)
    - `breakdowns.py`: Breakdown approval and task recycling (512 lines)
    - `agent_markers.py`: Agent task marker management (112 lines)
    - `task_notes.py`: Task notes persistence (102 lines)
    - `backpressure.py`: Queue limits, status, and scheduler checks (92 lines)
  - Added `_transition()` helper to eliminate repetitive lifecycle boilerplate
  - Lifecycle functions now take `task_id: str` instead of `task_path: Path | str`
  - Replaced `queue_utils.py` with re-export shim for backwards compatibility (41 lines)
  - Total line reduction: 2,711 → 1,957 lines (across 7 modules + shim, 28% reduction)

### Removed
- Deleted 8 legacy test files that tested deleted code (~2,670 lines total):
  - `tests/test_orchestrator_impl.py` (1,349 lines)
  - `tests/test_proposer_git.py` (342 lines)
  - `tests/test_compaction_hook.py` (263 lines)
  - `tests/test_tool_counter.py` (304 lines)
  - `tests/test_breakdown_context.py` (37 lines)
  - `tests/test_pre_check.py` (6 lines)
  - `tests/test_agent_env.py` (184 lines)
  - `tests/test_rebaser.py` (185 lines - tested unused rebaser functions)
- Trimmed dead code from `orchestrator/scheduler.py` (1,905 → 1,623 lines, -282 lines):
  - Removed 6 unused imports (shutil, Template, get_commands_dir, get_gatekeeper_config, get_gatekeepers, get_templates_dir, is_gatekeeper_enabled)
  - Removed 12 stub/dead functions:
    - `assign_qa_checks()` - stub that just returned
    - `process_auto_accept_tasks()` - stub that just returned
    - `process_gatekeeper_reviews()` - stub that just returned
    - `dispatch_gatekeeper_agents()` - stub that just returned
    - `detect_queue_health_issues()` - returned empty dict
    - `should_trigger_queue_manager()` - never called
    - `ensure_rebaser_worktree()` - never called (46 lines)
    - `check_branch_freshness()` - stub that just returned
    - `_is_branch_fresh()` - never called (40 lines)
    - `rebase_stale_branch()` - stub that just returned
    - `check_stale_branches()` - stub that just returned
    - `_count_commits_behind()` - never called (38 lines)
  - Cleaned HOUSEKEEPING_JOBS list (removed 6 stub function references)

### Changed
- Updated `get_agents()` docstring in `orchestrator/config.py` to reflect that only fleet format is supported (removed stale "Supports two formats" text)

### Documentation
- Added inline documentation in `orchestrator/scheduler.py` above `HOUSEKEEPING_JOBS` list explaining which housekeeping functions were removed and why (all were unimplemented stubs)

### Removed (from previous cleanup)
  - Removed `orchestrator/agent_scripts/` directory (replaced by agent directories)
  - Removed `orchestrator/prompts/implementer.md` (replaced by agent directory prompts)
  - Removed `commands/agent/` directory (replaced by agent directory instructions)
  - Removed `packages/client/src/roles/` directory (TypeScript roles not used)
  - Removed obsolete Python role files from `orchestrator/roles/` (only `base.py` and `github_issue_monitor.py` remain)
  - Removed `orchestrator/prompt_renderer.py` (no longer used)
  - Removed legacy fallback branches in `prepare_task_directory()` - agent directories are now required
  - Removed `setup_agent_commands()`, `generate_agent_instructions()`, `get_role_constraints()` functions
  - Removed `DEFAULT_AGENT_INSTRUCTIONS_TEMPLATE` constant
  - Removed legacy format support in `config.py` - fleet format is now the only supported format
  - Reduced scheduler.py from 2190 lines to 1905 lines (-285 lines, 13% reduction)

### Changed
- Migrated octopoid's own config to use agent directory structure (refactor-12):
  - Updated `.octopoid/agents.yaml` to new fleet format
  - Scaffolded `.octopoid/agents/implementer/` with agent.yaml, prompt.md, instructions.md, and scripts/
  - Scaffolded `.octopoid/agents/gatekeeper/` with full agent directory structure
  - Added agent.yaml to `.octopoid/agents/github-issue-monitor/`
  - Marked old files as DEPRECATED (kept for backward compatibility during migration):
    - `orchestrator/prompts/implementer.md`
    - `commands/agent/implement.md`
    - `orchestrator/agent_scripts/` (now has README explaining deprecation)
- Simplified fleet config format in agents.yaml (refactor-10):
  - New `fleet:` key replaces inline agent config with type references
  - Agent types reference directories in `packages/client/agents/<type>/` or `.octopoid/agents/<type>/`
  - Fleet entries can override type defaults (model, max_turns, etc.)
  - Custom agents supported via `type: custom` with explicit `path:`
  - Backward compatible: legacy `agents:` format still works
  - `get_agents()` now loads type defaults from `agent.yaml` and merges with fleet overrides
  - All agent configs include `agent_dir` key pointing to the agent directory
- Updated spawn strategies to read from agent directories (refactor-11):
  - `get_spawn_strategy()` now reads `spawn_mode` from agent config instead of hardcoding role names
  - `prepare_task_directory()` copies scripts from agent directory's `scripts/` folder
  - Prompt rendering uses `prompt.md` template from agent directory
  - `instructions.md` from agent directory is automatically included in prompt context
  - All changes gracefully fall back to legacy hardcoded paths when `agent_dir` is not set
  - Adding a new agent type now only requires creating a directory with `agent.yaml`, `prompt.md`, `instructions.md`, and `scripts/` - no scheduler code changes needed
- Added `AgentContext` dataclass to scheduler for structured per-agent state management (scheduler refactor phase 2, step 1/12)
- Extracted guard functions from scheduler agent loop into standalone, testable functions (scheduler refactor phase 2, step 2/12):
  - `guard_enabled`, `guard_not_running`, `guard_interval`, `guard_backpressure`, `guard_pre_check`, `guard_claim_task`
  - `AGENT_GUARDS` list and `evaluate_agent()` function for running the guard chain
  - Guards return `(should_proceed: bool, reason: str)` for composability
- Extracted housekeeping jobs into a list with fault isolation (scheduler refactor phase 2, step 3/12):
  - `HOUSEKEEPING_JOBS` list contains 10 independent housekeeping functions
  - `run_housekeeping()` function runs all jobs with try/except isolation
  - Failures in one job no longer prevent subsequent jobs from running
- Extracted spawn strategies from scheduler into standalone functions (scheduler refactor phase 3, step 4/12):
  - `spawn_implementer(ctx)` handles implementer spawn path (prepare task dir + invoke claude)
  - `spawn_lightweight(ctx)` handles lightweight agents (no worktree)
  - `spawn_worktree(ctx)` handles standard agents with worktrees
  - `get_spawn_strategy(ctx)` dispatches to the correct strategy based on agent type
  - `_init_submodule(agent_name)` extracted for orchestrator_impl submodule initialization
  - `_requeue_task(task_id)` helper for error recovery on spawn failure
- Refactored `run_scheduler()` to use pipeline architecture (scheduler refactor phase 2, step 5/12):
  - Replaced ~270-line monolithic function with ~75-line pipeline
  - Three-phase execution: pause check → housekeeping → evaluate + spawn agents
  - Each agent processed through: build context → evaluate guards → spawn strategy
  - Behaviour-identical to previous implementation (verified via debug logs and tests)
  - Simpler control flow: no nested if/else branches for spawn logic
  - Improved debuggability: guard failures logged with clear reason messages

### Added
- Comprehensive test suite for scheduler refactor (step 6/12):
  - New `tests/test_scheduler_refactor.py` with 28 unit tests
  - Tests cover: `AgentContext` dataclass, all 6 guard functions, `evaluate_agent` chain, `get_spawn_strategy` dispatch, `run_housekeeping` fault isolation
  - All existing scheduler tests continue to pass (behaviour-preserving refactor verified)
- Agent directory scaffolding in `octopoid init` (refactor-09):
  - `octopoid init` now copies agent type templates from `packages/client/agents/` to `.octopoid/agents/` in the user's project
  - Scaffolds both `implementer/` and `gatekeeper/` directories with all files and subdirectories
  - Preserves file permissions (executable scripts remain executable)
  - Skip logic prevents overwriting existing customizations on repeated init
  - Added `agents/` directory to package.json for npm distribution

### Fixed
- Unit tests now automatically mock `get_sdk()` to prevent production side effects when running `pytest tests/`
- `submit-pr` script now calls server submit endpoint directly, ensuring tasks transition from `claimed` to `provisional` even if agents don't exit immediately
- `handle_agent_result()` now uses state-first pattern to handle race conditions gracefully (expired leases, submit-pr races) and avoid incorrect function calls

### Added
- Per-task log files for lifecycle tracking (GH-3)
  - New `TaskLogger` class that creates persistent `.octopoid/logs/tasks/TASK-{id}.log` files
  - Logs all state transitions: CREATED, CLAIMED, SUBMITTED, ACCEPTED, REJECTED, FAILED, REQUEUED
  - Survives task completion for audit trail and debugging
  - Status script (`octopoid-status.py`) enhanced to show claim count and task log path (visible with `--verbose`)
  - Task detail view (`--task <id>`) shows full event history from task log
  - Comprehensive test coverage (17 tests in `tests/test_task_logger.py`)
- Breakdown depth tracking to prevent infinite re-breakdown loops (GH-10)
  - New `breakdown_depth` field on tasks (defaults to 0)
  - Configurable `max_breakdown_depth` in agents config (defaults to 1)
  - Breakdown agent now increments depth on subtasks and rejects at max depth
  - Task files now include `BREAKDOWN_DEPTH` metadata field
- `execution_notes` field for agent execution summaries (GH-13)
  - Auto-generated summaries include commit count, turn usage, and recent commit messages
  - Stored in database and returned via API
  - Full test coverage (13 tests) for generation, persistence, and API integration
- Hooks system for task lifecycle (`orchestrator/hooks.py`)
  - Declarative `before_submit` hooks: `rebase_on_main`, `create_pr`, `run_tests`
  - Per-task-type hook configuration via `task_types:` in config.yaml
  - Remediation support: hooks can return prompts for Claude to fix issues (e.g. rebase conflicts)
  - Default behavior unchanged: `before_submit: [create_pr]`
- Task `type` field for classifying tasks (e.g. "product", "infrastructure", "hotfix")
  - Migration `0004_add_task_type.sql` adds column to D1 database
  - Type field supported in create/update API endpoints
- Hook configuration in `.octopoid/config.yaml` (`hooks:` and `task_types:` keys)
- Config functions: `get_hooks_config()`, `get_task_types_config()`, `get_hooks_for_type()`
- Unit tests for hooks system (29 tests in `tests/test_hooks.py`)
- Unified configuration system (.octopoid/config.yaml) as single source of truth
- Dashboard now requires API server connection (v2.0 mode)
- GitHub issue monitor agent for automatic task creation from issues
- Production agents.yaml configuration with implementers and issue monitor
- SDK methods for task operations (create, claim, submit, delete, update)
- Title field to tasks table for better display in dashboards and UIs
- DELETE endpoint for tasks (API cleanup support)
- Cleanup script for removing test data (scripts/cleanup-test-data.py)
- DEVELOPMENT_RULES.md with guidelines for testing and database operations
- v2.0 API-only architecture rule (no database/file queue modes)

### Changed
- Improved `init` command UX with welcome message, cleaner output, and comprehensive post-init next steps guidance (GH-8)
  - Added welcome banner with project description
  - Summarized directory creation instead of listing every directory
  - Added counts for installed skills and gitignore entries
  - Post-init guidance now includes: CLAUDE.md setup, agent config, scheduler start (with single-run option), task creation, status commands, and documentation link
  - Skipped options now show how to enable them later
  - Added `--local` and `--server` mode selection flags (`--server` shows informative "not yet available" message)
  - Help text now documents deployment modes
- Dashboard is now API-only, removed local database mode
- Installation documentation updated to reflect source-only installation
- README updated with comprehensive troubleshooting section
- GitHub issue monitor now uses SDK to register tasks with API server
- Agents now use unified .octopoid/config.yaml for server configuration
- **BREAKING**: v2.0 is API-only architecture - no database mode, no file-based queue mode
- queue_utils.py refactored to API-only architecture (Phases 1-8 complete)
  - ✅ Phase 1: Foundation functions (get_task_by_id, list_tasks, find_task_by_id)
  - ✅ Phase 2: Critical path (claim_task, submit_completion, accept/reject_completion)
  - ✅ Phase 3: State management (fail_task, retry_task, reset_task, hold_task, mark_needs_continuation, resume_task)
  - ✅ Phase 4: Creation/deletion (create_task, complete_task, reject_task)
  - ✅ Phase 5: Review/feedback (already done in Phase 2)
  - ✅ Phase 6: Backpressure (separate module, not in queue_utils)
  - ✅ Phase 7: Complex workflows (use refactored functions, already API-compatible)
  - ✅ Phase 8: Projects (create_project, get_project, list_projects)
  - ✅ Additional: count_queue() now uses list_tasks (SDK)
  - 📝 Note: Some helper functions still have is_db_enabled() checks for backward compatibility
  - 📝 Note: Implementers can now claim and work on tasks via API (critical path complete)

### Fixed
- Dashboard now correctly connects to API server via SDK
- Reports module works in API-only mode (added missing Optional import)
- GitHub issue monitor creates tasks that are visible in API-connected dashboard

---

## Instructions for Agents

When you complete a task, add an entry to the **Unreleased** section above under the appropriate category:

- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** for vulnerability fixes

**Format:**
```markdown
- Brief description of change (#PR-number if applicable)
```

**Example:**
```markdown
### Added
- Task-specific logging system for better observability (#123)

### Fixed
- GitHub issue monitor now uses SDK instead of local files (#124)
```

Keep entries concise and user-focused. Focus on WHAT changed, not HOW.
