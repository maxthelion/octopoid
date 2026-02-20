# Changelog

All notable changes to Octopoid will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **GitHub Actions CI for integration tests** ([TASK-720c6196])
  - `.github/workflows/ci.yml`: Updated `unit-tests` job to use Python 3.13, set `PYTHONPATH` to repo root, and run `pytest tests/ --ignore=tests/integration` (fixes `ModuleNotFoundError: No module named 'orchestrator'`).
  - `.github/workflows/ci.yml`: Updated `integration-tests` job to use Python 3.13 and Node 20, start a fully local test server via wrangler dev (no Cloudflare cloud connection; uses workerd runtime locally), set `PYTHONPATH`, and run `pytest tests/integration/`.
  - Updated all jobs to use Node 20 (up from 18) and pnpm v9.

### Fixed

- **Fix test suite health issues** ([TASK-89cf1633])
  - `orchestrator/scheduler.py`: Fixed silent ImportError — `from .config import load_config` corrected to `from .config import _load_project_config as load_config`. This was silently breaking orchestrator registration.
  - `tests/integration/test_api_server.py`, `test_task_lifecycle.py`, `test_hooks.py`, `test_backpressure.py`, `test_claim_content.py`: Added `branch="main"` to all `sdk.tasks.create()` calls (46 calls) to match server requirement added 2026-02-17.
  - `tests/test_init.py`: Updated `test_skills_skipped_shows_hint` to assert `"install-commands"` instead of `"--skills"` to match current init output.
  - Removed `tests/test_queue_diagnostics.py` and `tests/test_queue_auto_fixes.py` (16 dead tests for a `diagnose_queue_health` script that no longer exists; replaced by `tests/integration/test_queue_health_diagnostics.py`).

- **Guard against spawning agents for empty task descriptions** ([TASK-16ffb5c4])
  - `orchestrator/scheduler.py`: Added `guard_task_description_nonempty` guard to `AGENT_GUARDS`. For scripts-mode agents, after claiming a task, the guard checks that `task["content"]` is non-empty and non-whitespace. If the task file is missing or empty, the task is moved to `failed` with a clear reason (e.g. "Task description is empty — no file at .octopoid/tasks/TASK-xxx.md") and no agent is spawned.
  - `orchestrator/tests/test_scheduler_lifecycle.py`: 8 unit tests covering all guard paths — no task, non-scripts mode, valid content, whitespace-only content, missing content field, empty file, reason message formatting, and SDK failure resilience.

- **Flow engine now owns task transitions — fixes child task orphaning and step failure orphaning** ([TASK-44d77f1f])
  - `orchestrator/scheduler.py`: `_handle_done_outcome` now calls `_perform_transition()` after steps succeed, mapping the flow YAML `to_state` to the correct API call (`submit` for `provisional`, `accept` for `done`, `update(queue=...)` for custom queues). The engine performs the transition — steps are pure side effects.
  - `orchestrator/scheduler.py`: `handle_agent_result` no longer silently swallows step exceptions. Exceptions propagate so `check_and_update_finished_agents` keeps the PID in tracking for retry. After 3 consecutive step failures, the task moves to `failed` and the counter resets.
  - `orchestrator/scheduler.py`: `check_and_update_finished_agents` only removes a PID from `running_pids.json` when result handling succeeds. On failure the PID is retained so the next scheduler tick retries.
  - `orchestrator/steps.py`: `submit_to_server` step deprecated to a no-op — the engine performs transitions, no step needs to call the API.
  - `.octopoid/flows/default.yaml`: Removed `submit_to_server` from `claimed -> provisional` runs. New list: `[push_branch, run_tests, create_pr]`.

### Added

- **Integration tests for queue health diagnostics** ([TASK-test-6-2])
  - `tests/integration/test_queue_health_diagnostics.py`: 5 integration tests covering `check_and_requeue_expired_leases()` against a real test server. Tests use `sdk` + `clean_tasks` for isolation and patch `datetime.now` in the scheduler to simulate expired leases (the server API does not allow setting `lease_expires_at` to past dates). Covers: stuck task detected and requeued (Test 1), healthy queue tasks left untouched (Test 2), and multiple stuck tasks all requeued in one pass (Test 3).

- **Project completion detection and PR creation** ([TASK-29d97975])
  - `orchestrator/scheduler.py`: Added `check_project_completion()` housekeeping job (60s interval). When all child tasks in an active project reach the `done` queue, the function creates a PR from the project's shared branch to the base branch via `gh pr create`, then updates the project status to `"review"` via `sdk.projects.update()`. Idempotent: skips projects already in `"review"` or `"completed"` status, and reuses existing PRs if one already exists for the branch. Added to `HOUSEKEEPING_JOB_INTERVALS`, `HOUSEKEEPING_JOBS`, and `run_scheduler()`.
  - `tests/test_check_project_completion.py`: 11 unit tests covering no-projects, incomplete tasks, all-done happy path, existing PR reuse, status skip guards, missing branch, SDK error resilience, PR creation failure, and multi-project isolation.

- **Extensible queue validation — relax TaskQueue types and sync flows to server** ([TASK-26ff1030])
  - `orchestrator/config.py`: `TaskQueue` is now `str` (validated at runtime by server); replaced `Literal` union with `BUILT_IN_QUEUES` set. `ACTIVE_QUEUES`, `PENDING_QUEUES`, `TERMINAL_QUEUES` now typed as `list[str]`.
  - `packages/shared/src/task.ts`: `TaskQueue` is now `string` with a `BUILT_IN_QUEUES` const and `BuiltInQueue` helper type.
  - `orchestrator/scheduler.py`: `_register_orchestrator()` now syncs all `.octopoid/flows/*.yaml` definitions to the server via `PUT /api/v1/flows/:name` after registration. Errors are non-fatal (logged only).
  - `packages/python-sdk/octopoid_sdk/client.py`: Added `FlowsAPI` namespace with `register(name, states, transitions)` method; available as `sdk.flows`.

- **Dashboard tab redesign — Tasks tab, server-sourced Drafts, remove PRs** ([TASK-dash-redesign])
  - Removed PRs tab (was calling `gh pr view` per-PR every 5s, burning GitHub API rate limit).
  - Added `_gather_drafts(sdk)` to `orchestrator/reports.py`: fetches drafts via `sdk.drafts.list()` and includes them in the project report.
  - Rewrote `packages/dashboard/tabs/drafts.py`: server-sourced data (no more filesystem scan); horizontal filter buttons (Active/Idea/Partial/Complete/Archived — Archived hidden by default); compact 1-line items with colored status tags (ACT green, IDEA cyan, PART orange, DONE gray, ARCH red); content panel reads file from `file_path` field.
  - Created `packages/dashboard/tabs/tasks.py`: new `TasksTab` with nested `TabbedContent` containing Done (existing `DoneTab`), Failed (new `FailedTab` filtering `final_queue=="failed"`), and Proposed (placeholder).
  - Updated `app.py`: removed `PRsTab`, replaced `DoneTab` with `TasksTab` at keybinding `t`; updated `_apply_report` accordingly.
  - Updated `dashboard.tcss`: removed PR styles; added inner `TabbedContent` height rules; added draft filter button and compact list item styles.
  - Final tab layout: `Work [W] | Inbox [I] | Agents [A] | Tasks [T] | Drafts [F]`

- **Task detail modal — Diff, Desc, Result, Logs tabbed content** ([TASK-0c3ec91c])
  - `TaskDetailModal` now shows a compact metadata header (ID, title, priority, agent, turns/PR) plus four tabbed content views ported from the old curses dashboard.
  - **Diff** tab: runs `git diff --stat origin/<base_branch>...HEAD` in the task's worktree.
  - **Desc** tab: reads `.octopoid/tasks/<task_id>.md`, falling back to the `## Task Description` section of `prompt.md`.
  - **Result** tab: reads and pretty-prints `.octopoid/runtime/tasks/<task_id>/result.json`.
  - **Logs** tab: reads `stdout.log` or `stderr.log` from the task runtime directory.
  - Content loads in a background thread (`@work(thread=True)`) so the UI never freezes on slow git diffs or large log files.
  - Graceful fallbacks for missing files (e.g. "no diff available", "no result yet").
  - Updated `dashboard.tcss` to set `TabPane { height: 1fr }` so tab content is visible.

- **Dashboard live turn counter for in-progress tasks** ([TASK-eb8c55a5])
  - `_read_live_turns()` in `reports.py` reads `.octopoid/agents/<instance_name>/tool_counter` file sizes to get live turn counts for all running agent instances.
  - `_gather_work()` now overlays live turn counts onto in-progress (claimed) task cards, replacing the always-zero `turns_used` value from the server.
  - Progress bar on task cards (e.g. `[████░░░░░░] 40/100t`) now fills proportionally as agents work, updating every 5 seconds with the dashboard poll.
  - No errors when tool_counter file doesn't exist (agent just started or not yet created).

### Fixed

- **Task detail modal crashes from Textual naming collision**
  - Renamed `self._task` to `self._task_data` on `TaskDetail` and `TaskDetailModal` to avoid collision with Textual's internal `_task` attribute (`asyncio.Task`), which caused `'_asyncio.Task' object has no attribute 'get'` errors.
  - Fixed `self.call_from_thread(update)` to `self.app.call_from_thread(update)` since `ModalScreen` doesn't have `call_from_thread` directly.

- **PID cleanup race condition orphaning finished tasks**
  - `_gather_agents()` in `reports.py` was calling `cleanup_dead_pids()` every 5 seconds (via dashboard polling), removing dead PIDs from `running_pids.json` before `check_and_update_finished_agents` could process results. Tasks got stuck in `claimed` with no process to finish them.
  - Replaced `cleanup_dead_pids()` + `load_blueprint_pids()` calls in `_gather_agents()` with read-only `count_running_instances()` + `get_active_task_ids()`.
  - Removed `cleanup_dead_pids` import from `guard_pool_capacity` in scheduler (cleanup now only happens in `check_and_update_finished_agents`).

- **Dashboard `octopoid-dash` script fails with `python: not found`**
  - Changed `exec python` to `exec python3` in the `octopoid-dash` launch script (macOS doesn't ship `python`).

- **Dashboard blank tabs after agent PRs**
  - Re-added `TabPane { height: 1fr }` rule to `dashboard.tcss` (Textual 8 needs explicit height or content resolves to 0).

- **Dashboard freeze on task card click**
  - `WorkTab.on_task_selected` was re-posting `TaskSelected` causing a message loop. Removed the handler; the message bubbles naturally from `WorkColumn` to `App`.

- **Dashboard `_gather_prs` burning GitHub API rate limit**
  - `_gather_prs()` called `gh pr list` then `gh pr view` per open PR every 5 seconds, exhausting the 5000 GraphQL calls/hour limit. Disabled the call (`"prs": []` in reports).

- **Dashboard error logging**
  - Added file logging to `.octopoid/logs/dashboard.log` via `__main__.py`.
  - Added `try/except` with logging around `_fetch_data` and `on_task_selected` in `app.py`.

- **Textual dashboard — kanban task card selection and navigation** ([TASK-ca13d073])
  - `WorkTab` now focuses the first column's `ListView` on mount and whenever the Work tab becomes active (`on_show`), making keyboard navigation available immediately.
  - `WorkColumn.on_key` handles `left`/`right` arrow keys to move focus between kanban columns.
  - Removed the unreachable `BINDINGS`/`action_select_task` from `WorkColumn` (the widget is not focusable; selection is handled via `on_list_view_selected`).
  - Removed `WorkTab.on_task_selected` which was re-posting `TaskSelected` unnecessarily — the message already bubbles naturally through the DOM to `OctopoidDashboard.on_task_selected`, preventing a double-modal bug.

### Changed

- **Textual dashboard — drafts list shows real IDs and relative age** ([TASK-c795a5a4])
  - `_DraftItem` now renders the server-assigned draft `id` (e.g. "42. Title") instead of a sequential index. Falls back to index+1 for filesystem-only drafts that have no server ID.
  - Each draft line now shows a compact, dim-styled relative age (e.g. "2h", "3d") derived from the file modification time (or `created_at` when server data is present).
  - `_load_drafts()` now captures file mtime as `created_at` so the age is always available even without server data.
  - Reuses `_format_age()` from `packages/dashboard/tabs/done.py` to avoid duplication.

### Added

- **Textual dashboard — swap-in and launch script update** ([TASK-dash-4])
  - Deleted `octopoid-dash.py` (2 050-line curses implementation) — fully replaced by `packages/dashboard/`.
  - Added `octopoid-dash` shell wrapper script: `./octopoid-dash` is now the recommended entry point and delegates to `python -m packages.dashboard`.
  - Updated `README.md` dashboard section to reference the new entry point.
  - Updated `docs/architecture.md` dashboard reference from `octopoid-dash.py` to `packages/dashboard/`.
  - Rewrote `tests/test_dashboard.py` for the Textual package: package importability, `DataManager.fetch_sync()`, `_format_age()`, tab `update_data()` interface, `TaskSelected` message, and wrapper script existence (32 tests, 0 skipped).

- **Textual dashboard — Done and Drafts tabs + task detail modal** ([TASK-dash-3])
  - `packages/dashboard/tabs/done.py`: Done tab with a DataTable showing completed, failed, and recycled tasks from the last 7 days. Columns: status icon (✓/✗/♻), ID, title, age, turns, commits, merge method, agent. j/k navigation; Enter opens task detail modal.
  - `packages/dashboard/tabs/drafts.py`: Drafts tab with master-detail layout — file list on the left (30%), full content on the right. Loads `project-management/drafts/*.md`; j/k navigates draft selection.
  - `packages/dashboard/widgets/task_detail.py`: `TaskDetail` widget and `TaskDetailModal` ModalScreen. Shows full task info: ID, title, role, priority, agent with status badge, turns, commits, PR link, and outcome/merge info for done tasks. Escape closes the modal.
  - Updated `app.py`: replaced placeholder labels with `DoneTab` and `DraftsTab`; wired both into `_apply_report()`; `on_task_selected` now pushes the `TaskDetailModal` (replaces the notification-only handler). All 6 tabs are now functional.
  - Extended `styles/dashboard.tcss` with styles for the Done table, Drafts layout (list panel, content panel), and draft list labels.

- **Textual dashboard — PRs, Inbox, and Agents tabs** ([TASK-dash-2])
  - `packages/dashboard/tabs/prs.py`: PRs tab with a DataTable showing open PR number, title, branch, age, and merge state.
  - `packages/dashboard/tabs/inbox.py`: Inbox tab with three columns — Proposals, Messages, and Drafts — each a scrollable ListView.
  - `packages/dashboard/tabs/agents.py`: Agents tab with master-detail layout — agent list on the left (name + status badge), detail pane on the right (role, status, current task, recent work, notes, blueprint metrics).
  - All three tabs wire into `_apply_report()` in `app.py` and refresh on every data poll.
  - Extended `styles/dashboard.tcss` with styles for section headers, PRs table, inbox columns, and agents layout.

- **Textual dashboard scaffold — Work tab** ([TASK-dash-1])
  - New `packages/dashboard/` package replacing the curses-based `octopoid-dash.py` (step 1 of 4).
  - `python -m packages.dashboard` launches a Textual TUI with 6-tab navigation (Work, PRs, Inbox, Agents, Done, Drafts).
  - Work tab renders a three-column kanban board: **INCOMING**, **IN PROGRESS**, **IN REVIEW** with real data from `orchestrator.reports.get_project_report()`.
  - In Progress cards display agent name, status badge (RUN / IDLE / BLOCK / PAUSE / ORPH), and a Unicode turns progress bar.
  - Keyboard shortcuts: `q` quit, `r` refresh, `w/p/i/a/d/f` switch tabs, up/down navigate tasks, Enter notifies task selection.
  - Data refreshes automatically every 5 seconds via a background thread worker.
  - Added `textual>=8.0.0` to `requirements.txt`.

- **Mock agent test infrastructure — step 4** ([TASK-mock-4])
  - `tests/integration/test_git_failure_scenarios.py`: 6 integration tests covering git failure error paths. Tests: `test_pr_merge_conflict_blocks_acceptance` (guard_pr_mergeable rejects CONFLICTING PR back to incoming), `test_merge_fails_at_merge_time` (gh pr merge failure → task moves to failed), `test_push_branch_failure` (deleted remote → task stays in claimed), `test_push_branch_no_diff` (already-pushed branch → graceful "up-to-date" success → provisional), `test_rejected_task_gets_rebase_instructions` (rejection reason includes git rebase with correct base branch), `test_conflict_after_rejection` (reject cycle → re-claim → still conflicting → back to incoming, no stuck state).

- **Mock agent test infrastructure — step 3** ([TASK-mock-3])
  - `tests/integration/test_scheduler_mock.py`: 7 integration tests exercising full scheduler lifecycles using mock agents against the real local test server. No Claude API calls, no real GitHub API (uses fake `gh`). Tests cover: happy-path full cycle (implementer → provisional → gatekeeper approve → done), agent failure/crash → failed queue, gatekeeper reject → incoming, multiple rejections, edge cases (minimal commits, needs_continuation).
  - Uses `clean_tasks` fixture to avoid stale-task interference (claim endpoint does not filter by scope, so tasks from previous tests must be deleted before each test).

- **Mock agent test infrastructure — step 2** ([TASK-mock-2])
  - `tests/fixtures/conftest_mock.py`: pytest fixtures for local git repos — `test_repo` (bare remote + working clone), `conflicting_repo` (diverging branches on same file), `task_dir` (full scheduler task directory structure).
  - `tests/fixtures/mock_helpers.py`: `run_mock_agent()` helper that runs `mock-agent.sh` with configurable `MOCK_*` and `GH_MOCK_*` env vars and fake `gh` on PATH.
  - `tests/test_mock_fixtures.py`: expanded to 20 smoke tests covering all fixture combinations.

- **Mock agent test infrastructure — step 1** ([TASK-mock-1])
  - `tests/fixtures/mock-agent.sh`: configurable shell script that simulates agent behavior (implementer and gatekeeper modes) without calling Claude. Controlled via `MOCK_OUTCOME`, `MOCK_DECISION`, `MOCK_COMMENT`, `MOCK_REASON`, `MOCK_COMMITS`, `MOCK_CRASH`, and `MOCK_SLEEP` env vars.
  - `tests/fixtures/bin/gh`: fake `gh` CLI that returns controlled responses for `pr create`, `pr view`, `pr merge`, and `pr list`. Logs all calls to `GH_MOCK_LOG` when set.
  - `tests/test_mock_fixtures.py`: 15 smoke tests covering all outcome/decision combinations, commit counts, crash mode, and all fake gh commands.

### Fixed

- **Pool model: prevent duplicate instances working the same task** ([TASK-pool-dedup-claim])
  - Added `get_active_task_ids(blueprint_name)` to `orchestrator/pool.py` — returns the set of task IDs currently held by alive instances of a blueprint.
  - Added dedup check in `guard_claim_task` in `orchestrator/scheduler.py`: after claiming a task, if another running instance of the same blueprint is already working on it, the claim is released and the guard returns `False` so no second instance is spawned.
  - Covers the bug where `guard_pool_capacity` sees spare capacity and spawns a second gatekeeper instance on an already-claimed task.

### Changed

- **Updated `/enqueue` skill for v2.0 API-only architecture** ([TASK-fix-enqueue-skill])
  - Replaced manual file-writing instructions with `create_task()` from `orchestrator.tasks`
  - Updated task file location from `.octopoid/runtime/shared/queue/incoming/` to `.octopoid/tasks/`
  - Removed hardcoded `BRANCH: main` default (branch now comes from config via `get_base_branch()`)
  - Removed obsolete `EXPEDITE` and `SKIP_PR` fields
  - Updated examples to reflect current task field set

### Added

- **Project flow system step 4: end-to-end integration test** ([TASK-projfix-4])
  - Added `tests/integration/test_project_lifecycle.py` with 7 tests covering the full project lifecycle against the real local server
  - Tests verify: project creation with branch, child task association via top-level `project_id` field, `/projects/{id}/tasks` endpoint returning correct tasks, child tasks completing without individual PRs (child_flow semantics), project transitioning to provisional when all children are done
  - Uses `clean_tasks` fixture for test isolation; documents that `project_id` must be a top-level field (not inside `metadata`) for the server FK constraint and `/projects/{id}/tasks` to work

- **Project flow system step 3: auto-inherit project branch on task creation** ([TASK-projfix-3])
  - `create_task()` in `orchestrator/tasks.py` now fetches the project via SDK when `project_id` is given but `branch` is not, using `project["branch"]` automatically
  - Explicit `branch=` argument always takes precedence over the project branch
  - Falls back to `get_base_branch()` if project has no branch set or SDK fetch fails
  - Added `tests/test_create_task_project_branch.py` with 4 tests covering inheritance, override, no-project, and no-branch-on-project cases

- **Project flow system step 2: child_flow dispatch in scheduler** ([TASK-projfix-2])
  - `handle_agent_result_via_flow()` in `scheduler.py` now checks `task.get("project_id")`: if set and the flow has a `child_flow`, uses `child_flow` transitions instead of top-level transitions
  - `_handle_done_outcome()` applies the same logic so implementer agents on child tasks run `rebase_on_project_branch, run_tests` instead of `push_branch, create_pr, submit_to_server`
  - Added unit tests in `orchestrator/tests/test_scheduler_lifecycle.py` covering both paths (child task with `project_id` and normal task without)

- **Project flow system step 1** ([TASK-projfix-1])
  - Added `rebase_on_project_branch` step to `orchestrator/steps.py`: fetches project's shared branch via SDK and rebases worktree, ensuring each child task sees previous children's work
  - Created `.octopoid/flows/project.yaml` with `child_flow` definition for multi-task projects (children skip `create_pr`, commit to shared branch)
  - `create_flows_directory()` in `flow.py` now generates both `default.yaml` and `project.yaml`

- **Pool model step 4: reports and flow validation** ([TASK-7ac764e6])
  - `_gather_agents()` in `reports.py` updated to use pool model: iterates blueprints, calls `cleanup_dead_pids` + `load_blueprint_pids`, and reports `running_instances`, `max_instances`, `idle_capacity`, and `current_tasks` per blueprint.
  - `_gather_health()` in `reports.py` updated to count capacity via `count_running_instances()` summed across all blueprints instead of reading `state.json` per agent.
  - `Condition.validate()` and `Transition.validate()` in `flow.py` now accept agent references by `name`, `blueprint_name`, or `role` (previously only by `name` in conditions, by `name` or `role` in transitions).
  - Removed dead `guard_not_running` function from `scheduler.py` (superseded by `guard_pool_capacity` in step 3).

- **Pool model step 3: scheduler blueprint iteration and pool guard** ([TASK-6b1d5556])
  - Added `guard_pool_capacity` to replace `guard_not_running` in `AGENT_GUARDS`. Calls `cleanup_dead_pids` then checks `count_running_instances < max_instances`.
  - Added `_next_instance_name` helper that generates `{blueprint_name}-{N}` names for spawned instances.
  - `spawn_implementer`, `spawn_lightweight`, `spawn_worktree` now call `register_instance_pid` after spawning to track the new process in `running_pids.json`.
  - `check_and_update_finished_agents` rewritten to iterate blueprint dirs via `load_blueprint_pids()` instead of scanning agent state files. Dead PIDs trigger result handling and are removed from pool tracking.

- **Pool model step 2: PID tracking per blueprint** ([TASK-5e5eebd1])
  - Added `orchestrator/pool.py` with 6 functions for per-blueprint PID tracking: `get_blueprint_pids_path`, `load_blueprint_pids`, `save_blueprint_pids`, `count_running_instances`, `register_instance_pid`, `cleanup_dead_pids`.
  - `running_pids.json` lives at `.octopoid/runtime/agents/<blueprint_name>/running_pids.json` and tracks `{pid: {task_id, started_at, instance_name}}`.
  - Writes are atomic via tempfile + rename to prevent corruption under concurrent access.
  - `count_running_instances` uses `os.kill(pid, 0)` to count only live processes.
  - `cleanup_dead_pids` removes stale entries and returns the count removed.
  - Added `tests/test_pool_tracking.py` with unit tests covering all 6 functions.

- **Scheduler poll endpoint integration + per-job intervals** ([TASK-cd01c12d])
  - Added `sdk.poll(orchestrator_id)` to `packages/python-sdk/octopoid_sdk/client.py` — single `GET /api/v1/scheduler/poll` call returns `queue_counts`, `provisional_tasks`, and `orchestrator_registered` in one request.
  - Added `queue_counts: dict | None` field to `AgentContext`; `guard_backpressure()` uses pre-fetched counts when available, eliminating per-agent `count_queue()` API calls.
  - `can_claim_task()` in `backpressure.py` accepts an optional `queue_counts` dict to skip individual API calls.
  - `_register_orchestrator()` now accepts `orchestrator_registered: bool` and skips the POST when the poll response confirms registration.
  - `process_orchestrator_hooks()` accepts `provisional_tasks: list | None`; when provided, skips `sdk.tasks.list(queue="provisional")`.
  - Added `scheduler_state.json` persistence for per-job `last_run` timestamps; helper functions `load_scheduler_state()`, `save_scheduler_state()`, `is_job_due()`, `record_job_run()` in `scheduler.py`.
  - Added `HOUSEKEEPING_JOB_INTERVALS` dict with per-job intervals: `check_and_update_finished_agents=10s`, `agent_evaluation_loop=60s`, `process_orchestrator_hooks=60s`, `check_and_requeue_expired_leases=60s`, `_register_orchestrator=300s`, `_check_queue_health_throttled=1800s`.
  - `run_scheduler()` now calls jobs directly with interval checks; fetches poll data once per 60s tick shared across all remote jobs.
  - Reduced launchd `StartInterval` from 300s to 10s in `com.octopoid.scheduler.plist`.
  - Added `_run_agent_evaluation_loop()` helper that accepts `queue_counts` from poll.
  - Added 27 unit tests in `tests/test_scheduler_poll.py` covering poll-based backpressure, interval management, register skip, and pre-fetched hooks.

- **Rich task detail view in dashboard** ([TASK-rich-detail])
  - `_render_work_detail()` in `octopoid-dash.py` redesigned with a split layout: compact top summary panel (ID, title, role, priority, agent, status, turns, commits), a navigable left sidebar menu, and a scrollable right content area.
  - Four content views: **Diff** (runs `git diff --stat` in the task's worktree against the base branch), **Desc** (reads `.octopoid/tasks/<ID>.md`, falling back to the `## Task Description` section of `prompt.md`), **Result** (pretty-prints `result.json`), and **Logs** (shows `stdout.log` or `stderr.log`).
  - Navigation: `h`/`l` or left/right arrows switch focus between sidebar and content; `j`/`k` or up/down arrows navigate the focused panel; `r` clears the content cache and force-refreshes; `Esc`/`q` return to the board.
  - Added `detail_menu_index`, `detail_scroll_offset`, and `detail_focus` fields to `DashboardState`.
  - Added `_get_detail_content()` with a module-level cache to avoid re-running subprocesses on every render frame.
  - Graceful fallbacks when worktrees, task files, or log files don't exist.

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
