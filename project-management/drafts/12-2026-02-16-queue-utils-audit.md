---
**Processed:** 2026-02-18
**Mode:** human-guided
**Actions taken:**
- Verified refactor is complete: queue_utils.py reduced from 2,711 lines to 41-line re-export shim
- Confirmed all proposed modules exist: sdk.py, tasks.py, projects.py, breakdowns.py, agent_markers.py, task_notes.py, backpressure.py, config.py
- Confirmed dead v1 functions deleted (parse_task_file, resolve_task_file, get_queue_subdir)
- Assessed remaining re-export shim — 10 production files + 7 test files still import via shim. User decided shim is harmless tech debt, not worth a cleanup task.
**Outstanding items:** none
---

# queue_utils.py Audit: 2,711 Lines of Everything

**Status:** Complete
**Captured:** 2026-02-16

## What is it?

`orchestrator/queue_utils.py` is the largest Python file in the project at 2,711 lines. It's the god module — everything that touches tasks, projects, or the queue goes through it. It has **62 top-level functions** spanning at least 8 unrelated concerns.

## What does it do?

It does everything:

### 1. SDK / Setup (2 functions)
`get_sdk()`, `get_orchestrator_id()` — initialise the SDK client and register with the server.

### 2. Queue Path Helpers (11 functions)
`get_queue_subdir()`, `find_task_file()`, `count_queue()`, `count_open_prs()`, `can_create_task()`, `can_claim_task()`, `list_tasks()`, `parse_task_file()`, `resolve_task_file()`, `get_queue_status()`, `get_projects_dir()`

These are a mix of SDK wrappers and **file-based queue operations from v1**. Many still reference local queue directories even though everything now goes through the API. For example, `find_task_file()` searches local queue subdirectories as a "legacy fallback", and `count_queue()` has both an SDK path and a file-counting path.

### 3. Task Lifecycle (16 functions)
`claim_task()`, `unclaim_task()`, `complete_task()`, `submit_completion()`, `accept_completion()`, `reject_completion()`, `fail_task()`, `reject_task()`, `retry_task()`, `reset_task()`, `hold_task()`, `resume_task()`, `review_reject_task()`, `mark_needs_continuation()`, `approve_and_merge()`

This is the core state machine. Each function moves a task between queue states. Many of these are **nearly identical in structure**: read task file, call SDK to update queue, write result back. The pattern is:

```python
def <verb>_task(task_path, ...):
    task_id = extract_id(task_path)
    sdk = get_sdk()
    sdk.tasks.update(task_id, queue='<new_state>', ...)
    # maybe write something to the task file
    return task_path
```

There's significant repetition — the same "get SDK, call update, handle error" pattern appears in most of these 16 functions.

### 4. Task CRUD (5 functions)
`create_task()`, `get_task_by_id()`, `find_task_by_id()`, `is_task_still_valid()`

`create_task()` alone is ~130 lines because it handles both the SDK call and local file creation with template rendering.

### 5. Task Markers (6 functions)
`write_task_marker()`, `read_task_marker_for()`, `clear_task_marker_for()`, `read_task_marker()`, `clear_task_marker()`, `_get_agent_state_dir()`

Agent-to-task assignment tracking via JSON files in the agent state directory. This is its own concern — nothing to do with the queue.

### 6. Task Notes (4 functions)
`_generate_execution_notes()`, `get_task_notes()`, `save_task_notes()`, `cleanup_task_notes()`

Progress notes for tasks. Again, its own concern.

### 7. Projects (8 functions)
`create_project()`, `_write_project_file()`, `get_project()`, `list_projects()`, `activate_project()`, `get_project_tasks()`, `get_project_status()`, `send_to_breakdown()`

An entire project management system embedded inside queue_utils. `get_project()` reads from local YAML files (a known issue from the postmortem — draft 13).

### 8. Breakdown (8 functions)
`get_breakdowns_dir()`, `list_pending_breakdowns()`, `approve_breakdown()`, `_create_and_push_branch()`, `_parse_breakdown_tasks()`, `is_burned_out()`, `recycle_to_breakdown()`

Task decomposition and recycling system. `approve_breakdown()` alone is ~110 lines. `recycle_to_breakdown()` is ~150 lines.

### 9. Reviews (4 functions)
`_insert_rejection_feedback()`, `get_review_feedback()`, `escalate_to_planning()`

## What's wrong?

### Mixed paradigms
The module serves two masters: the **SDK/API** (server-side state) and **local files** (queue directories, task markdown files, YAML project files). Many functions do both — call the SDK AND write to disk. This made sense during the migration from file-based to API-based queues, but now it's just confusion. The file operations should go away.

### Repetitive lifecycle functions
The 16 task lifecycle functions are variations on the same theme. Most could be replaced by a single `transition_task(task_id, new_queue, **kwargs)` plus specific pre/post hooks for the ones that do extra work (like `approve_and_merge` which also merges PRs).

### Unrelated concerns crammed together
Task markers, notes, projects, breakdowns — these have nothing to do with "queue utils". They ended up here because queue_utils was the first module that existed, and everything got added to it.

### Dead v1 code
Functions like `parse_task_file()` (parses frontmatter from markdown task files), `resolve_task_file()`, `get_queue_subdir()` are v1 artefacts. The server is the source of truth now. Local task files exist for agent context, not for queue state.

## Who calls it?

Post-refactor, the main consumers are:
- `orchestrator/scheduler.py` — uses `claim_task`, `list_tasks`, `get_sdk`, task markers, continuation tasks
- `orchestrator/roles/github_issue_monitor.py` (via `base.py`) — uses `claim_task`, `complete_task`, `fail_task`
- `orchestrator/reports.py` — uses `count_queue`, `list_tasks`
- `orchestrator/git_utils.py` — uses `get_project`
- `orchestrator/pr_utils.py` — uses `create_task`
- `orchestrator/planning.py` — uses `parse_task_file`, `create_task`
- Agent scripts — **don't use it at all**. They write `result.json` and let the scheduler handle transitions.

## Suggested split

| New module | Functions | Lines (est.) |
|-----------|-----------|-------------|
| `orchestrator/sdk.py` | `get_sdk()`, `get_orchestrator_id()` | ~100 |
| `orchestrator/task_lifecycle.py` | All 16 lifecycle functions, refactored to reduce repetition | ~400 |
| `orchestrator/task_crud.py` | `create_task()`, `get_task_by_id()`, `find_task_by_id()` | ~250 |
| `orchestrator/projects.py` | All 8 project functions | ~300 |
| `orchestrator/breakdowns.py` | All 8 breakdown functions | ~400 |
| `orchestrator/agent_markers.py` | All 6 marker functions | ~100 |
| `orchestrator/task_notes.py` | All 4 notes functions | ~150 |
| **Delete** | `parse_task_file()`, `resolve_task_file()`, `get_queue_subdir()`, file-based queue helpers | ~200 deleted |

Total: ~1,700 lines in focused modules vs 2,711 lines in one file. The ~1,000 line reduction comes from eliminating repetition in lifecycle functions and deleting dead v1 helpers.

## Open Questions

- How much of the file-based code is still needed? If the server is the source of truth, most local file operations could go.
- Should the lifecycle functions be methods on a Task class rather than standalone functions?
- Is the breakdown system still used? It may be another dead subsystem.
- How does this interact with the declarative flows idea (draft 17)? If flows define the state machine, the lifecycle functions become simpler.
