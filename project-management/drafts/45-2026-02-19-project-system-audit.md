# Project System Audit: 40% Deployed, Scheduler Integration Missing

**Status:** Idea
**Captured:** 2026-02-19

## Raw

> The pool model steps 1-4 should be a project with a shared feature branch. Explored whether the project system actually works — it might be left behind after recent changes.

## Findings

### What works

| Component | Status |
|-----------|--------|
| Server API (CRUD, FK validation) | Working |
| Python SDK (`ProjectsAPI`) | Working |
| `orchestrator/projects.py` (all functions use server API) | Working |
| Task creation with `project_id` | Working |
| Worktree branch handling (tasks inherit branch) | Working |
| Integration tests (2-task shared branch) | Passing |
| Gatekeeper project vs standalone detection | Working |
| Breakdown → project task creation | Working |

### What's missing

| Component | Status |
|-----------|--------|
| Scheduler project logic (sequencing, flow dispatch) | Missing |
| `project.yaml` flow (never deployed to `.octopoid/flows/`) | Missing |
| `child_flow` dispatch in scheduler | Dead code — field exists on Flow dataclass but never read |
| `rebase_on_project_branch` step | Missing — referenced in flow template but not in `steps.py` |
| Auto-inherit project branch on task creation | Missing |

### The gap

The project system is **architecturally sound but operationally incomplete**. Server, SDK, and plumbing are there. But the scheduler treats project tasks as standalone:
- No shared branch workflow activates
- Each task creates its own PR (instead of committing to shared branch)
- No sequential execution enforcement
- `child_flow` is defined in `flow.py` but the scheduler never reads it

### Specific issues

1. **`project.yaml` never created** — `create_flows_directory()` in `flow.py` only creates `default.yaml`. The `generate_project_flow()` function exists (lines 370-403) but is never called.

2. **`child_flow` never dispatched** — `handle_agent_result_via_flow()` loads the flow YAML and finds transitions, but never checks `flow.child_flow`. Even if `project.yaml` existed, the scheduler wouldn't use it for child tasks.

3. **Missing steps** — The project flow template references `rebase_on_project_branch` which has no `@register_step` implementation in `steps.py`. Would raise `ValueError: Unknown step` if the flow ever ran.

4. **No branch inheritance** — `create_task(project_id=X)` doesn't auto-fetch the project's branch. Tasks need explicit `branch=` or they'd use the default.

5. **`flows.md` acknowledges this** — Line 103: "**Not Yet Implemented** — Project flows... Draft 42"

### Impact on pool model tasks

The 4 pool model steps (TASK-861f0682, TASK-5e5eebd1, TASK-6b1d5556, TASK-7ac764e6) **cannot use the project system as-is**. They would be treated as standalone tasks, each creating separate PRs. The shared branch / sequential execution benefit wouldn't activate.

Options:
1. **Fix projects first** — Deploy `project.yaml`, wire `child_flow` in scheduler, implement `rebase_on_project_branch`. Then use projects for pool model.
2. **Manual sequencing** — Keep tasks as standalone but use `blocked_by` to sequence them. Each creates its own PR against `feature/client-server-architecture`. Merge in order.
3. **Single large task** — Combine all 4 steps into one task. Simpler but risks the agent running out of turns.

## Recommended path

Option 2 (manual sequencing with `blocked_by`) is the pragmatic choice for the pool model steps right now. Fixing the project system is its own body of work — probably 3-4 tasks:

1. Deploy `project.yaml` flow + implement `rebase_on_project_branch` step
2. Wire `child_flow` dispatch in scheduler (check `task.project_id`, use child flow)
3. Auto-inherit project branch on task creation
4. End-to-end integration test: project with 2 sequential tasks through full lifecycle

## Related

- Draft 42: "Deploy Project Flow" (idea)
- Draft 21: "Fix Project Lifecycle" (partial)
- `docs/flows.md` line 103: acknowledges project flows not yet implemented
- `tests/test_project_lifecycle.py` — integration tests that DO pass (worktree sharing works mechanically)
