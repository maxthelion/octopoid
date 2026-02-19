# Fix Project System: Deploy Flow, Wire Scheduler, Implement Steps

**Status:** Idea
**Captured:** 2026-02-19

## Raw

> Projects are 40% deployed (draft 45 audit). Server/SDK work, scheduler doesn't use any of it. Need to close the gap so multi-task work like the pool model steps can use shared branches.

## Tasks to Enqueue

### Task 1: Deploy project flow + implement project steps

- Call `generate_project_flow()` in `create_flows_directory()` to write `.octopoid/flows/project.yaml`
- Implement `@register_step("rebase_on_project_branch")` in `orchestrator/steps.py` — rebase worktree onto the project's branch before running tests
- The child flow skips `create_pr` (children commit to shared branch directly); only the project itself creates a PR when all children complete

### Task 2: Wire child_flow dispatch in scheduler

- In `handle_agent_result_via_flow()`, check if task has `project_id`
- If yes, load the flow's `child_flow` transitions instead of the top-level transitions
- The `Flow.child_flow` field already exists on the dataclass but is never read
- Test: project task with `project_id` uses child_flow transitions; standalone task uses normal transitions

### Task 3: Auto-inherit project branch on task creation

- When `create_task(project_id=X)` is called without explicit `branch=`, fetch the project and use `project.branch`
- Both in `orchestrator/tasks.py` and verify the server stores it correctly
- Test: create task with project_id, verify branch matches project.branch

### Task 4: End-to-end integration test

- Create project → create 2 tasks with project_id → claim task 1 → complete → claim task 2 → verify it sees task 1's commits → complete → verify project completion triggers PR
- Use `scoped_sdk` fixture, real local server
- This is the acceptance test for the whole feature

## Sequencing

Task 1 → Task 2 → Task 3 → Task 4 (each blocked_by the previous)

## Related

- Draft 45: Project system audit
- Draft 42: Deploy project flow (earlier idea)
- Draft 21: Fix project lifecycle (partial)
- `docs/flows.md` line 103: "Not Yet Implemented — Project flows"
