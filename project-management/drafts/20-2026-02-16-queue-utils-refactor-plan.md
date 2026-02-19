# queue_utils.py Refactor: Entity Modules with Common Base

**Status:** Idea
**Captured:** 2026-02-16

## Raw

> refactor queue utils in the refactoring branch. tasks, projects, drafts etc moved out into separate files from a common entity base. remove as much of filesystem stuff as possible. simplify lifecycle in line with hooks. Add it if it simplifies the approach.

## The Problem

`queue_utils.py` is 2,711 lines with 62 functions spanning 8+ unrelated concerns (see draft 19). It mixes task lifecycle, project management, breakdowns, agent markers, notes, and dead v1 filesystem code in a single file. The lifecycle functions are particularly repetitive — 16 functions that all follow the same pattern:

```python
def <verb>_task(task_path, ...):
    task_path = Path(task_path)
    task_info = parse_task_file(task_path)      # read from disk
    task_id = task_info["id"]
    sdk = get_sdk()
    sdk.tasks.<method>(task_id, ...)            # call API
    with open(task_path, "a") as f:             # append to local file
        f.write(f"\nVERB_AT: {datetime.now()}\n")
    cleanup_task_worktree(task_id, ...)         # maybe cleanup
    return task_path
```

Every function: parses a file, extracts an ID, calls the SDK, appends metadata to a file nobody reads, and maybe cleans up. The local file writes are vestigial — the API is the source of truth.

## Design

### Core Idea: Entity Modules with a Common SDK Base

Split queue_utils into focused modules, each owning one entity type. All share a common `sdk.py` module for SDK access.

```
orchestrator/
  sdk.py              # get_sdk(), get_orchestrator_id() — shared by all
  tasks.py            # Task lifecycle, CRUD, queries
  projects.py         # Project CRUD, activation, status
  breakdowns.py       # Breakdown parsing, approval, recycling
  agent_markers.py    # Task marker files for agent state tracking
  task_notes.py       # Agent notes across attempts
  backpressure.py     # can_create_task(), can_claim_task(), count_queue()
  queue_utils.py      # DELETED (or kept as thin re-export shim during migration)
```

### Module Details

#### `sdk.py` (~100 lines)

Extracted from queue_utils. Two functions:
- `get_sdk()` — lazy-init SDK from config/env
- `get_orchestrator_id()` — register with server, cache result

Every other module imports from here. No circular deps.

#### `tasks.py` (~500 lines, down from ~1,200)

All task operations. The key simplification: **drop the `task_path` parameter pattern**. Most lifecycle functions accept `task_path`, parse it to get `task_id`, then call the SDK. The file path is irrelevant — the SDK has the ID. New signatures take `task_id` directly.

Before:
```python
def fail_task(task_path: Path | str, error: str) -> Path:
    task_path = Path(task_path)
    task_info = parse_task_file(task_path)
    task_id = task_info["id"]
    sdk = get_sdk()
    sdk.tasks.update(task_id, queue="failed")
    logger = get_task_logger(task_id)
    logger.log_failed(error=error[:200])
    with open(task_path, "a") as f:           # DELETE THIS
        f.write(f"\nFAILED_AT: ...")          # DELETE THIS
    cleanup_task_worktree(task_id)
    return task_path
```

After:
```python
def fail_task(task_id: str, error: str) -> dict:
    sdk = get_sdk()
    sdk.tasks.update(task_id, queue="failed")
    get_task_logger(task_id).log_failed(error=error[:200])
    cleanup_task_worktree(task_id)
    return {"task_id": task_id, "action": "failed"}
```

**Lifecycle simplification with a transition function:**

Most lifecycle functions are variations of "move task to queue X with optional side effects". Extract the common pattern:

```python
def _transition(task_id: str, queue: str, *, cleanup_worktree: bool = False,
                push_commits: bool = False, log_fn=None, **sdk_kwargs) -> dict:
    """Move a task to a new queue with optional side effects."""
    sdk = get_sdk()
    result = sdk.tasks.update(task_id, queue=queue, **sdk_kwargs)
    if log_fn:
        log_fn(get_task_logger(task_id))
    if cleanup_worktree:
        cleanup_task_worktree(task_id, push_commits=push_commits)
    return result
```

Then the public functions become thin wrappers that add specific logic only where needed:

```python
def unclaim_task(task_id: str) -> dict:
    return _transition(task_id, "incoming", claimed_by=None)

def fail_task(task_id: str, error: str) -> dict:
    return _transition(task_id, "failed", cleanup_worktree=True,
                       log_fn=lambda l: l.log_failed(error=error[:200]))

def accept_completion(task_id: str, accepted_by: str = None) -> dict:
    result = _transition(task_id, "done", cleanup_worktree=True, push_commits=True,
                         log_fn=lambda l: l.log_accepted(accepted_by=accepted_by or "unknown"))
    cleanup_task_notes(task_id)
    return result
```

Functions that have real unique logic (`claim_task`, `create_task`, `submit_completion`, `review_reject_task`, `approve_and_merge`) stay as full implementations.

**What stays in tasks.py:**
- `claim_task()` — complex (SDK claim + file reading + logging)
- `create_task()` — complex (file creation + SDK registration + hooks)
- `submit_completion()` — has execution notes generation
- `review_reject_task()` — has rejection feedback insertion + escalation logic
- `approve_and_merge()` — has hook execution
- `_transition()` — shared helper
- Simple wrappers: `unclaim_task`, `complete_task`, `accept_completion`, `reject_completion`, `fail_task`, `reject_task`, `retry_task`, `reset_task`, `hold_task`, `mark_needs_continuation`, `resume_task`
- Query functions: `find_task_by_id`, `get_task_by_id`, `get_continuation_tasks`, `list_tasks`, `is_task_still_valid`

**What gets deleted from tasks.py:**
- All `with open(task_path, "a")` appends — nobody reads these. The API is the source of truth.
- `parse_task_file()` — only needed in `claim_task` and `create_task` for reading content. Can be simplified to a plain file read.
- `resolve_task_file()` — legacy path resolution, replace with direct `get_tasks_file_dir() / filename`
- `find_task_file()` — legacy queue-subdir search
- `get_queue_subdir()` — legacy directory structure
- `ALL_QUEUE_DIRS` — legacy constant
- `_insert_rejection_feedback()` — move to `review_reject_task` as inline logic or a local helper
- `_generate_execution_notes()` — move inline to `submit_completion`

#### `projects.py` (~200 lines, down from ~300)

All project operations. Currently projects are YAML-file-based with TODO comments about using the SDK. The SDK already has `sdk.projects.list()`. This module should:

- Use `sdk.projects.list()` for listing (if server supports it), falling back to YAML for now
- Keep `_write_project_file()` as the local visibility layer
- Move `send_to_breakdown()` here since it creates projects

Functions:
- `create_project()`
- `get_project()`
- `list_projects()`
- `activate_project()`
- `get_project_tasks()`
- `get_project_status()`
- `send_to_breakdown()`

#### `breakdowns.py` (~350 lines)

Breakdown parsing and approval. Self-contained.

Functions:
- `get_breakdowns_dir()`
- `list_pending_breakdowns()`
- `approve_breakdown()`
- `_create_and_push_branch()`
- `_parse_breakdown_tasks()`
- `is_burned_out()`
- `recycle_to_breakdown()`

#### `agent_markers.py` (~80 lines)

Agent-to-task assignment tracking. These are filesystem operations (JSON files in agent state dirs), which is fine — they're agent-local state, not queue state.

Functions:
- `_get_agent_state_dir()`
- `write_task_marker()`
- `read_task_marker_for()`
- `clear_task_marker_for()`
- `read_task_marker()`
- `clear_task_marker()`

#### `task_notes.py` (~100 lines)

Agent notes across attempts. Also filesystem-based but that's appropriate — these are ephemeral working files.

Functions:
- `get_task_notes()`
- `save_task_notes()`
- `cleanup_task_notes()`
- `NOTES_STDOUT_LIMIT`

#### `backpressure.py` (~80 lines)

Queue counting and backpressure checks. These are thin SDK wrappers.

Functions:
- `count_queue()`
- `count_open_prs()` — could also move to a `github.py` but it's only used for backpressure
- `can_create_task()`
- `can_claim_task()`
- `get_queue_status()`

### Filesystem Operations to Delete

These local file writes exist throughout the lifecycle functions and serve no purpose now that the API is the source of truth:

| Pattern | Occurrences | Action |
|---------|-------------|--------|
| `with open(task_path, "a"): f.write("VERB_AT: ...")` | ~12 functions | **Delete** — API tracks timestamps |
| `parse_task_file()` to extract task_id | ~12 functions | **Replace** — take `task_id` as parameter |
| `resolve_task_file()` path resolution | ~3 functions | **Simplify** — `get_tasks_file_dir() / filename` |
| `find_task_file()` queue subdir search | ~2 functions | **Delete** — all files are in `.octopoid/tasks/` now |
| `get_queue_subdir()` | ~2 functions | **Delete** — only used by `recycle_to_breakdown` |
| PR count file cache `_get_pr_cache_path()` | 1 function | **Keep** — caching `gh pr list` is still useful |

### Hooks Integration

The `approve_and_merge()` function currently uses the old `hooks.py` system (`HookPoint`, `HookContext`, `run_hooks`). The `hook_manager.py` is the newer system. These should be consolidated:

1. `approve_and_merge()` stays in `tasks.py` but switches to `HookManager`
2. `create_task()` already uses `HookManager.resolve_hooks_for_task()` — this stays
3. The old `hooks.py` (`run_hooks`, `BUILTIN_HOOKS`) can be deprecated once `approve_and_merge` switches

This aligns with the declarative flows idea (draft 17) — the hook system is the embryo of flows. By cleaning it up now, we make the later flows work easier.

### Caller Migration

The main consumers and what changes for them:

| Consumer | Current import | New import |
|----------|---------------|------------|
| `scheduler.py` | `from . import queue_utils` | `from . import tasks, agent_markers, backpressure` |
| `scheduler.py` (inner) | `from .queue_utils import get_sdk` | `from .sdk import get_sdk` |
| `git_utils.py` | `from .queue_utils import get_project` | `from .projects import get_project` |
| `planning.py` | `from .queue_utils import parse_task_file, create_task` | `from .tasks import create_task` |
| `pr_utils.py` | `from .queue_utils import create_task` | `from .tasks import create_task` |
| `reports.py` | `from .queue_utils import list_tasks, count_queue` | `from .tasks import list_tasks` + `from .backpressure import count_queue` |
| `cli.py` | `from .queue_utils import get_sdk` | `from .sdk import get_sdk` |
| Scripts | `from orchestrator.queue_utils import ...` | `from orchestrator.tasks import ...` etc |
| Tests | `from orchestrator.queue_utils import ...` | Update imports |

### Migration Strategy: Re-export Shim

To avoid a big-bang migration, keep `queue_utils.py` as a thin re-export shim:

```python
"""Backwards-compatible re-exports. Import from specific modules instead."""
from .sdk import get_sdk, get_orchestrator_id
from .tasks import (claim_task, unclaim_task, complete_task, submit_completion,
                    accept_completion, reject_completion, fail_task, ...)
from .projects import (create_project, get_project, list_projects, ...)
from .breakdowns import (approve_breakdown, recycle_to_breakdown, ...)
from .agent_markers import (write_task_marker, read_task_marker_for, ...)
from .task_notes import (get_task_notes, save_task_notes, cleanup_task_notes)
from .backpressure import (count_queue, can_create_task, can_claim_task, ...)
```

This means existing callers keep working. New code imports from specific modules. The shim can be removed in a future cleanup.

## Estimated Line Counts

| Module | Lines | Notes |
|--------|-------|-------|
| `sdk.py` | ~100 | Extracted as-is |
| `tasks.py` | ~500 | Down from ~1,200 via `_transition()` and deleting file writes |
| `projects.py` | ~200 | Extracted as-is, minor cleanup |
| `breakdowns.py` | ~350 | Extracted as-is |
| `agent_markers.py` | ~80 | Extracted as-is |
| `task_notes.py` | ~100 | Extracted as-is |
| `backpressure.py` | ~80 | Extracted as-is |
| `queue_utils.py` (shim) | ~30 | Re-exports only |
| **Total** | **~1,440** | **Down from 2,711 (47% reduction)** |

The reduction comes from:
- Deleting ~12 local file write blocks (~200 lines)
- Replacing 16 lifecycle functions with `_transition()` + thin wrappers (~400 lines saved)
- Deleting dead v1 helpers: `parse_task_file`, `resolve_task_file`, `find_task_file`, `get_queue_subdir`, `ALL_QUEUE_DIRS` (~150 lines)
- Removing redundant docstrings, try/except boilerplate, and normalising return types (~200 lines)

## Implementation Order

This is a single-task job if we use the re-export shim (no caller changes needed in the same PR):

1. Create `sdk.py` — extract `get_sdk()` and `get_orchestrator_id()`
2. Create `tasks.py` — extract lifecycle + CRUD, add `_transition()`, delete file writes
3. Create `projects.py` — extract project functions
4. Create `breakdowns.py` — extract breakdown functions
5. Create `agent_markers.py` — extract marker functions
6. Create `task_notes.py` — extract notes functions
7. Create `backpressure.py` — extract counting/limits functions
8. Replace `queue_utils.py` body with re-export shim
9. Run tests, fix breakages
10. Update `scheduler.py` to import from new modules directly (optional, can be separate PR)

## Open Questions

- Should `parse_task_file()` be kept at all? It's used by `claim_task` to read file content, and by `planning.py`. If we keep it, it goes in `tasks.py` as a private helper.
- Should `count_open_prs()` move to a `github.py` module? It's only used for backpressure but it's really a GitHub operation.
- The re-export shim keeps backwards compatibility but adds an import indirection. Is it worth it, or should we just update all imports in one go?
- Should this be done before or after the declarative flows work (draft 17)? Doing it first gives flows a cleaner foundation.

## Relationship to Other Drafts

- **Draft 17 (Declarative Flows)**: This refactor gives flows a clean task lifecycle to hook into. The `_transition()` function is a natural place to check flow-defined rules.
- **Draft 18 (Codebase Size Audit)**: This directly addresses the #1 target (queue_utils.py, 2,711 lines).
- **Draft 19 (queue_utils Audit)**: This is the implementation of the split proposed there.
