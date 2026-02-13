# Hooks System for Octopoid Task Lifecycle

## Context

Currently the implementer's post-work actions (create PR, submit to provisional) are hardcoded in two places in `implementer.py`. Rebase logic lives only in the scheduler and rejects tasks back to incoming on conflict instead of letting the agent fix them. The user wants a declarative hooks system where lifecycle actions are configurable, testable, and have sensible defaults. The agent should be autonomous — if rebase fails, it should fix conflicts, not just punt.

Additionally, hooks should be driven by **task type** — e.g., product tasks go through QA, infrastructure tasks skip QA. Task type also determines which agents (implementers, gatekeepers) work on it.

## Approach

Create a lightweight hooks module with:
- **Hook points**: `before_submit` (agent-side) and `before_merge` (scheduler-side, future)
- **Built-in hooks**: `rebase_on_main`, `create_pr`, `run_tests`
- **Task types**: tasks carry a `type` field; hooks are resolved per-type
- **Declarative config** in `.octopoid/config.yaml` under `hooks:` and `task_types:` keys
- **Remediation**: hooks can return a prompt that Claude executes to fix issues, then the hook retries
- **Defaults**: `before_submit: [create_pr]` matches current behavior exactly

## Configuration Design

```yaml
# .octopoid/config.yaml

# Default hooks (apply to all tasks unless overridden by type)
hooks:
  before_submit:
    - rebase_on_main
    - create_pr

# Task type definitions — override hooks and agent assignment per type
task_types:
  product:
    hooks:
      before_submit:
        - rebase_on_main
        - run_tests
        - create_pr
      before_merge:
        - gatekeeper: [qa, architecture]
    # Which agents can work on this type
    agents:
      implementer: [implementer-1, implementer-2]
      gatekeeper: [gatekeeper-qa]

  infrastructure:
    hooks:
      before_submit:
        - rebase_on_main
        - create_pr
      # No before_merge — auto-accept
    agents:
      implementer: [implementer-1]

  hotfix:
    hooks:
      before_submit:
        - run_tests
        - create_pr
    agents:
      implementer: [implementer-1, implementer-2]
```

Hook resolution order:
1. Task has `type` field → use `task_types.<type>.hooks`
2. No type or type has no hooks → use top-level `hooks:`
3. No top-level hooks → use `DEFAULT_HOOKS` (just `create_pr`)

Agent filtering: when claiming tasks, agents check if they're allowed to work on that task's type. Unconfigured types are open to all agents.

## Files to Create

### 1. `orchestrator/hooks.py` — Core hooks module

Data types:
- `HookPoint` enum: `BEFORE_SUBMIT`, `AFTER_SUBMIT`, `BEFORE_MERGE`, `AFTER_MERGE`
- `HookStatus` enum: `SUCCESS`, `FAILURE`, `SKIP`
- `HookResult` dataclass: `status`, `message`, `context` dict, optional `remediation_prompt`
- `HookContext` dataclass: `task_id`, `task_title`, `task_path`, `task_type`, `branch_name`, `base_branch`, `worktree`, `agent_name`, `commits_count`, `extra` dict

Built-in hook functions (all take `HookContext`, return `HookResult`):
- `hook_rebase_on_main` — fetch main, check if rebase needed, attempt rebase. On conflict: abort rebase, return FAILURE with `remediation_prompt` telling Claude to resolve conflicts
- `hook_create_pr` — push branch, call `git_utils.create_pull_request()` (reuses existing at `orchestrator/git_utils.py:436`)
- `hook_run_tests` — detect test runner, run tests. On failure: return FAILURE with `remediation_prompt` showing test output

Registry and resolution:
- `BUILTIN_HOOKS: dict[str, HookFn]` mapping names to functions
- `DEFAULT_HOOKS = {"before_submit": ["create_pr"], ...}`
- `resolve_hooks(hook_point, task_type, agent_name)` — resolves type config → project config → defaults → per-agent overrides to ordered list of hook functions
- `run_hooks(hook_point, ctx, agent_name)` → `(all_ok, results)` — executes in order, fail-fast on first failure

### 2. `tests/test_hooks.py` — Unit tests

- Test each hook in isolation with mocked subprocess
- Test config loading: defaults, project-level, type-level, per-agent overrides
- Test `run_hooks` runner: empty hooks succeed, fail-fast on failure, skip handling
- Test remediation_prompt is set on rebase conflict
- Test type resolution: task with type uses type hooks, task without type uses defaults

## Files to Modify

### 3. `orchestrator/roles/implementer.py`

**In `run()` (lines 766-809)**: Replace inline PR creation + `submit_completion` with hooks pipeline:
```python
from ..hooks import HookPoint, HookContext, HookStatus, run_hooks

hook_ctx = HookContext(
    task_id=task_id, task_title=task_title, task_path=task_path,
    task_type=task.get("type"), branch_name=branch_name,
    base_branch=base_branch, worktree=self.worktree,
    agent_name=self.agent_name, commits_count=commits_made,
)

all_ok, results = run_hooks(HookPoint.BEFORE_SUBMIT, hook_ctx, self.agent_name)

# Handle remediation (e.g. rebase conflicts → invoke Claude to fix)
if not all_ok:
    for result in results:
        if result.remediation_prompt:
            self.invoke_claude(result.remediation_prompt, max_turns=20)
            all_ok, results = run_hooks(HookPoint.BEFORE_SUBMIT, hook_ctx, self.agent_name)
            break
    if not all_ok:
        mark_needs_continuation(task_path, reason="hook_failure", ...)
        return 0

# Extract PR URL from hook results
for r in results:
    if "pr_url" in r.context:
        self._store_pr_in_db(task_id, r.context["pr_url"])

submit_completion(task_path, commits_count=commits_made, turns_used=turns_used)
```

**In `_handle_implementation_result()` (lines 466-522)**: Same pattern replaces the duplicate PR creation block. The `skip_pr` direct-merge path (lines 426-464) stays as-is — it's a special case.

Error handling for failed exit codes (lines 377-390) and no-changes detection (lines 396-403) stay OUTSIDE hooks — hooks only run when there's actual work to submit.

### 4. `orchestrator/config.py`

Add following the existing pattern (like `get_gatekeeper_config()`):
- `DEFAULT_HOOKS_CONFIG` dict
- `get_hooks_config()` — project-level hooks
- `get_task_types_config()` — task type definitions
- `get_hooks_for_type(task_type)` — resolve hooks for a specific task type

### 5. Task data model — add `type` field

- `packages/shared/src/task.ts`: Add `type?: string | null` to `Task` interface, `type?: string` to `CreateTaskRequest` and `UpdateTaskRequest`
- `packages/server/migrations/0004_add_task_type.sql`: `ALTER TABLE tasks ADD COLUMN type TEXT`
- `orchestrator/queue_utils.py`: Pass `type` through in `claim_task()` response (already comes from API)

### 6. `.octopoid/config.yaml`

Add hooks and task_types sections (see Configuration Design above).

### 7. `packages/client/templates/config.yaml`

Add commented-out hooks and task_types documentation to the template.

## What This Does NOT Change (Phase 1)

- Scheduler processing of provisional tasks — stays as explicit function calls
- Queue utils state transitions — unchanged
- Gatekeeper role — unchanged (scheduler-side `before_merge` hooks are future work)
- Pre-check role — unchanged
- Agent claim filtering by type — future work (initially, any agent can claim any type)

Scheduler-side hooks (`before_merge`) and type-based agent filtering are defined in config but not wired up in phase 1. The foundation is there for phase 2.

## Verification

1. **No hooks configured** → default `before_submit: [create_pr]` → same behavior as today
2. **`before_submit: [rebase_on_main, create_pr]`** → agent rebases then creates PR
3. **Rebase conflict** → agent gets Claude to fix conflicts, retries rebase, then creates PR
4. **Task with type** → uses type-specific hooks from config
5. **Task without type** → uses project-level hooks → defaults
6. **Unit tests** → `pytest tests/test_hooks.py` passes with mocked subprocess
7. **Integration** → start test server, create typed task, run scheduler, verify correct hooks fire
