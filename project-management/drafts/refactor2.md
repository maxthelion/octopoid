# Agent Architecture Refactor

## Context

The current architecture has three problems:
1. **Import resolution bugs** — 950-line `implementer.py` runs in worktrees where Python imports the committed (stale) orchestrator code
2. **Ad-hoc hooks** — Hook lifecycle is scattered across implementer.py, hooks.py, and queue_utils.py with no server enforcement
3. **Ad-hoc repo operations** — Git operations are duplicated across implementer.py, hooks.py, and git_utils.py with no testable abstraction

This refactor introduces: script-based agents (no Python in worktree), server-enforced hooks, a HookManager, and a RepoManager.

---

## Part 1: RepoManager

**New file:** `orchestrator/repo_manager.py`

Consolidates all repo operations into a testable class. Currently scattered across:
- `git_utils.py` (37 functions, low-level)
- `implementer.py` `_reset_worktree()` (lines 198-241, ad-hoc)
- `implementer.py` skip_pr merge (lines 494-515, ad-hoc)
- `hooks.py` `hook_create_pr()`, `hook_rebase_on_main()`, `hook_merge_pr()` (inline git/gh calls)

```python
class RepoManager:
    def __init__(self, worktree: Path, base_branch: str = "main"):
        self.worktree = worktree
        self.base_branch = base_branch

    # --- Status ---
    def get_status(self) -> RepoStatus:
        """branch, commits_ahead, has_uncommitted, head_ref"""

    # --- Branch & commit ---
    def push_branch(self) -> str:
    def rebase_on_base(self) -> RebaseResult:  # success/conflict/up-to-date
    def reset_to_base(self) -> None:

    # --- PR lifecycle ---
    def create_pr(self, title: str, body: str = "") -> PrInfo:  # idempotent
    def merge_pr(self, pr_number: int, method: str = "merge") -> bool:

    # --- Submodule ---
    def push_submodule(self, name: str) -> None:
    def stage_submodule_pointer(self, name: str) -> None:
```

Delegates to existing `git_utils.py` functions where possible. New structured return types (`RepoStatus`, `RebaseResult`, `PrInfo`) as dataclasses.

**Tests:** `tests/test_repo_manager.py` — unit tests with mocked git commands.

---

## Part 2: Server-Side Hooks

### Hook data model

When a task is created, hooks are resolved from config and stored in the task:

```json
{
  "hooks": [
    {"name": "run_tests",  "point": "before_submit", "type": "agent",       "status": "pending"},
    {"name": "create_pr",  "point": "before_submit", "type": "agent",       "status": "pending"},
    {"name": "merge_pr",   "point": "before_merge",  "type": "orchestrator", "status": "pending"}
  ]
}
```

**Hook types:**
- `agent` — Must be completed by the agent before it finishes. Supplied as scripts in the task directory.
- `orchestrator` — Run by the orchestrator/scheduler during state transitions.

### Server changes

**Migration 0005:** Add `hooks TEXT` column to tasks table (JSON string).

**API changes to `packages/server/src/routes/tasks.ts`:**

1. **Task creation** (`POST /api/v1/tasks`) — Accept optional `hooks` field, store as JSON.

2. **New endpoint: Record hook evidence** (`POST /api/v1/tasks/:id/hooks/:hookName/complete`)
   - Body: `{ "status": "passed"|"failed", "evidence": { ... } }`
   - Updates the hook's status in the stored hooks JSON
   - Returns updated task

3. **State transitions** (`POST /api/v1/tasks/:id/submit`, `/accept`, etc.)
   - Before allowing transition, check that all hooks for the relevant point have `status != "pending"`
   - Return `400` with `{ "error": "hooks_incomplete", "pending": [...] }` if not met
   - The `hooks` field in the response always includes current hook statuses

### Hook resolution at task creation

When the orchestrator creates a task (via `queue_utils.create_task()` or the GitHub issue monitor), it resolves hooks from config and includes them:

```python
hooks = resolve_hooks_for_task(task_type)  # from .octopoid/config.yaml
sdk.tasks.create(id=..., hooks=hooks, ...)
```

The existing `config.get_hooks_config()` and `config.get_hooks_for_type()` are reused for resolution. A new `resolve_hooks_for_task()` function converts from the config format to the hook data model.

---

## Part 3: HookManager

**New file:** `orchestrator/hook_manager.py`

Orchestrator-side abstraction for managing the hook lifecycle. Used by the scheduler.

```python
class HookManager:
    def __init__(self, sdk: OctopoidSDK, repo_manager_factory: Callable):
        self.sdk = sdk
        self.repo_manager_factory = repo_manager_factory

    def resolve_hooks_for_task(self, task_type: str | None) -> list[dict]:
        """Resolve hooks from config for a new task."""

    def get_pending_hooks(self, task: dict, point: str, hook_type: str) -> list[dict]:
        """Get hooks that still need to be completed."""

    def run_orchestrator_hook(self, task: dict, hook: dict) -> HookEvidence:
        """Execute an orchestrator-side hook (e.g., merge_pr)."""

    def record_evidence(self, task_id: str, hook_name: str, evidence: HookEvidence):
        """Record hook completion evidence with the server."""

    def can_transition(self, task: dict, target_queue: str) -> tuple[bool, list[str]]:
        """Check if all hooks are satisfied for a transition. Returns (ok, pending_hooks)."""
```

**Orchestrator hooks** (run by HookManager, not agents):
- `merge_pr` — uses RepoManager to merge
- Future: `deploy_staging`, `notify_slack`, etc.

**Tests:** `tests/test_hook_manager.py` — unit tests with mocked SDK.

---

## Part 4: Script-Based Agents

### Task directory structure
```
.orchestrator/tasks/{task_id}/
  worktree/           # git worktree (Claude's cwd)
  task.json           # task metadata from API (including hooks)
  prompt.md           # rendered prompt
  env.sh              # shell env vars for reference
  scripts/
    submit-pr         # Python: RepoManager.create_pr() + record hook evidence
    finish            # Python: record evidence + API transition
    fail              # Python: API fail transition
    record-progress   # Python: append to notes
    run-tests         # Python: detect & run tests + record hook evidence
  result.json         # outcome (written by scripts, read by scheduler)
  notes.md            # progress notes
  stdout.log / stderr.log
```

### Agent scripts — Python with explicit paths

Each script uses `#!/path/to/.venv/bin/python` shebang. The scheduler templates the shebang and PYTHONPATH when copying scripts to the task directory.

Scripts import `RepoManager` and use `curl`/SDK for API calls. Example `submit-pr`:

```python
#!/path/to/.venv/bin/python
"""Push branch and create PR for this task."""
import json, os, sys
sys.path.insert(0, os.environ["ORCHESTRATOR_PYTHONPATH"])

from orchestrator.repo_manager import RepoManager

repo = RepoManager(
    worktree=Path(os.environ["WORKTREE"]),
    base_branch=os.environ["BASE_BRANCH"],
)

# Push and create PR
pr = repo.create_pr(
    title=f"[{os.environ['TASK_ID']}] {os.environ['TASK_TITLE']}",
)

# Record hook evidence with server
record_hook_evidence(os.environ["TASK_ID"], "create_pr", {
    "status": "passed",
    "pr_url": pr.url,
    "pr_number": pr.number,
})

# Write result
write_result({"outcome": "submitted", "pr_url": pr.url, ...})
```

Script templates live in `orchestrator/agent_scripts/`. The scheduler copies them to `{task_dir}/scripts/`, replacing the shebang with the actual venv path and setting `ORCHESTRATOR_PYTHONPATH`.

### Which hooks become which scripts

| Current hook | Hook type | Becomes |
|-------------|-----------|---------|
| `rebase_on_main` | agent | `scripts/rebase` (or prompt instruction — Claude can rebase itself) |
| `run_tests` | agent | `scripts/run-tests` |
| `create_pr` | agent | `scripts/submit-pr` |
| `merge_pr` | orchestrator | `HookManager.run_orchestrator_hook()` |

### Prompt template

**New file:** `orchestrator/prompts/implementer.md`

Template rendered with `string.Template`. Key sections:
- Task details (id, title, content, priority, branch)
- Available scripts with usage examples
- Agent hooks to complete (from task.hooks where type=agent)
- Global instructions (from `.orchestrator/global-instructions.md`)
- Implementation instructions
- Optional: rejection context, review feedback, continuation notes

The prompt explicitly tells the agent which hooks it must complete:
```
## Required Steps Before Finishing
You must complete these steps before calling submit-pr:
1. Run tests: `../scripts/run-tests`
2. Submit PR: `../scripts/submit-pr`
```

**New file:** `orchestrator/prompt_renderer.py` — single function to render the template with task data.

### Scheduler changes

**New functions in `scheduler.py`:**

1. `prepare_task_directory(task, agent_name, agent_config) -> Path`
   - Create directory structure
   - Call `create_task_worktree(task)` (existing)
   - Write task.json, env.sh
   - Copy + template scripts from `orchestrator/agent_scripts/`
   - Render prompt.md via `prompt_renderer`
   - Copy agent commands to `.claude/commands/` (existing `setup_agent_commands`)

2. `invoke_claude(task_dir, agent_config) -> int`
   - `claude -p "$(cat prompt.md)" --allowedTools Read,Write,Edit,Glob,Grep,Bash,Skill --max-turns N --model M`
   - `cwd=worktree`, env from env.sh
   - Returns PID

3. `handle_agent_result(task_id, agent_name, task_dir)`
   - Read result.json → handle outcome
   - No result.json → check for progress → continuation or fail
   - Check stderr for auth errors → requeue

4. Result handling integrated into `check_and_update_finished_agents()`

**Feature flag:** `agent_mode: scripts` in agents.yaml (default: `python` for backward compat).

### Scheduler hook integration

When the scheduler processes results:
1. Agent finishes → scheduler reads result.json
2. If `outcome: submitted` → check `hook_manager.can_transition(task, "provisional")`
3. If hooks satisfied → call API submit (server validates too)
4. If hooks not satisfied → log error (shouldn't happen if scripts recorded evidence)

For orchestrator hooks (e.g., `merge_pr` on BEFORE_MERGE):
1. Task reaches provisional → scheduler picks it up
2. `hook_manager.get_pending_hooks(task, "before_merge", "orchestrator")` → `[merge_pr]`
3. `hook_manager.run_orchestrator_hook(task, hook)` → uses RepoManager
4. `hook_manager.record_evidence(task_id, "merge_pr", evidence)`
5. `hook_manager.can_transition(task, "done")` → True → accept task

---

## Implementation Order

### Phase 1: Foundations (no changes to existing code)

| Step | File | What |
|------|------|------|
| 1a | `orchestrator/repo_manager.py` | RepoManager class wrapping git_utils |
| 1b | `tests/test_repo_manager.py` | Unit tests |
| 1c | `orchestrator/hook_manager.py` | HookManager class |
| 1d | `tests/test_hook_manager.py` | Unit tests |
| 1e | `orchestrator/prompt_renderer.py` | Prompt template rendering |
| 1f | `orchestrator/prompts/implementer.md` | Prompt template |
| 1g | `orchestrator/agent_scripts/submit-pr` | Python script using RepoManager |
| 1h | `orchestrator/agent_scripts/run-tests` | Python script for test execution |
| 1i | `orchestrator/agent_scripts/finish` | Python script for task completion |
| 1j | `orchestrator/agent_scripts/fail` | Python script for task failure |
| 1k | `orchestrator/agent_scripts/record-progress` | Python script for notes |

### Phase 2: Server-side hooks

| Step | File | What |
|------|------|------|
| 2a | `packages/server/migrations/0005_add_hooks.sql` | Add hooks column |
| 2b | `packages/server/src/routes/tasks.ts` | Store hooks on create, new evidence endpoint, transition validation |
| 2c | Integration tests for hook enforcement |

### Phase 3: Wire into scheduler

| Step | File | What |
|------|------|------|
| 3a | `orchestrator/scheduler.py` | Add `prepare_task_directory()`, `invoke_claude()`, `handle_agent_result()` |
| 3b | `orchestrator/scheduler.py` | Feature flag dispatch (`agent_mode: scripts`) |
| 3c | `orchestrator/queue_utils.py` | Include hooks in `create_task()` |
| 3d | `.orchestrator/agents.yaml` | Add `agent_mode: scripts` to one agent |

### Phase 4: Test end-to-end

1. Set `implementer-2` to `agent_mode: scripts`
2. Requeue a task → verify task dir created correctly
3. Verify Claude gets prompt with task content + script paths
4. Verify scripts work (submit-pr creates PR, records evidence)
5. Verify server validates hooks before transition
6. Verify continuation (kill Claude mid-task)
7. Switch all implementers to scripts mode

### Phase 5: Remove old code

- `orchestrator/roles/implementer.py` — delete
- `orchestrator/hooks.py` — delete (replaced by HookManager + scripts)
- `orchestrator/roles/base.py` — slim down (keep for other roles if needed)
- Scheduler — remove PYTHONPATH hacks, old spawn path

---

## Files summary

| File | Action |
|------|--------|
| `orchestrator/repo_manager.py` | **Create** |
| `orchestrator/hook_manager.py` | **Create** |
| `orchestrator/prompt_renderer.py` | **Create** |
| `orchestrator/prompts/implementer.md` | **Create** |
| `orchestrator/agent_scripts/submit-pr` | **Create** |
| `orchestrator/agent_scripts/run-tests` | **Create** |
| `orchestrator/agent_scripts/finish` | **Create** |
| `orchestrator/agent_scripts/fail` | **Create** |
| `orchestrator/agent_scripts/record-progress` | **Create** |
| `tests/test_repo_manager.py` | **Create** |
| `tests/test_hook_manager.py` | **Create** |
| `packages/server/migrations/0005_add_hooks.sql` | **Create** |
| `packages/server/src/routes/tasks.ts` | **Modify** |
| `orchestrator/scheduler.py` | **Modify** |
| `orchestrator/queue_utils.py` | **Modify** (include hooks in create_task) |
| `.orchestrator/agents.yaml` | **Modify** (agent_mode flag) |

## Existing code reused

- `git_utils.py` — All functions stay, wrapped by RepoManager
- `config.get_hooks_config()`, `config.get_hooks_for_type()` — Hook resolution
- `scheduler.claim_and_prepare_task()` — Task claiming
- `scheduler.setup_agent_commands()` — Claude Code skill setup
- `git_utils.create_task_worktree()` — Worktree creation
- `queue_utils.get_review_feedback()` — For prompt rendering

## Immediate prerequisite

Commit pending orchestrator changes (`base.py`, `git_utils.py`, `github_issue_monitor.py`) so existing agents work while we build alongside.
