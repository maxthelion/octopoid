# Consolidate flow runs and task hooks into single execution path

**Captured:** 2026-02-24

## Raw

> consolidate flow runs and task hooks into single execution path

## Problem

Two parallel systems execute steps during task transitions, with neither being complete:

**Flow runs** (`transition.runs` in YAML) — the declarative system. Defines what happens during transitions. Executed by `handle_agent_result_via_flow` → `execute_steps`. Missing `rebase_on_base`, and has only a nuclear `except Exception → failed` error handler.

**Task hooks** (`hooks` field on tasks) — the older imperative system. Denormalized onto each task at creation time. Executed by `process_orchestrator_hooks` → `HookManager`. Has `rebase_on_base` and graceful error handling (rebase failures → reject to incoming). But never runs for the default flow because `_has_flow_blocking_conditions` skips tasks with gatekeeper conditions.

They've drifted:
- Flow says: `[post_review_comment, check_ci, merge_pr]`
- Hooks say: `[rebase_on_base, merge_pr]`
- Neither is the complete picture

### Incident that exposed this

TASK-0048b341 (extract housekeeping.py): gatekeeper approved PR #213, `handle_agent_result_via_flow` ran the flow's `transition.runs`, but `merge_pr` hit a rebase conflict. Because the flow path has no `rebase_on_base` step and no graceful rebase error handling, the task was dumped into `failed` via the nuclear fallback instead of being rejected back to incoming for retry.

Meanwhile, `process_orchestrator_hooks` has exactly the right error handling for this case (lines 1230-1256) but never ran because the default flow has a gatekeeper condition.

## Proposed fix

### 1. Flow runs become the single source of truth

Add `rebase_on_base` to the flow's `provisional -> done` runs:

```yaml
"provisional -> done":
    conditions:
      - name: gatekeeper_review
        type: agent
        agent: gatekeeper
        on_fail: incoming
    runs: [post_review_comment, check_ci, rebase_on_base, merge_pr]
```

### 2. Add proper error handling to execute_steps / handle_agent_result_via_flow

Rebase/merge failures during `execute_steps` should reject the task back to `on_fail` (incoming), not crash into the nuclear `failed` fallback. Lift the error handling pattern from `process_orchestrator_hooks` lines 1230-1256:
- Post rejection message with rebase failure details to task thread
- Call `sdk.tasks.reject()` to requeue to incoming
- Log the failure clearly

This could be done by:
- A) Making `execute_steps` raise a specific `RebaseConflictError` that `handle_agent_result_via_flow` catches and handles gracefully
- B) Adding step-level error handling inside `execute_steps` itself
- C) Wrapping the `execute_steps` call in `handle_agent_result_via_flow` with rebase-aware error handling

### 3. Deprecate task-level hooks field

Stop stamping hooks onto tasks at creation time. The flow already defines the steps. The hooks field is a denormalized copy that drifts from the flow definition.

### 4. Retire process_orchestrator_hooks

For flows with agent conditions: `handle_agent_result_via_flow` handles everything after the agent finishes.

For flows without agent conditions (auto-accept): `handle_agent_result_via_flow` already handles the implementer's result and runs steps. If there's a need for a polling trigger (auto-transition with no agent at all), that could be a small scheduler job that checks for condition-free transitions — but it should use `execute_steps`, not a separate HookManager path.

## Context

Discovered while investigating why TASK-0048b341 ended up in `failed` despite being approved by the gatekeeper. The gatekeeper approved, the flow ran merge_pr without rebasing first, merge_pr hit a conflict, and the generic error handler dumped the task into failed instead of rejecting gracefully.

See also: draft #145 (lease recovery bug in provisional) — another instance where the provisional queue handling has edge cases from split code paths.

## No per-task customization exists today

Investigation confirmed that hooks are **not** per-task customizable. `resolve_hooks_for_task()` in `hook_manager.py` reads from `config.yaml` (project-level `hooks:` key) or falls back to `DEFAULT_HOOKS`. Every task gets the same hooks — the `hooks` field on a task is just a snapshot of the project-level config at creation time.

This means consolidating into flow runs loses no capability. Flow `transition.runs` already serves the same purpose (project-level step definitions), and is actually more granular — steps are defined per-transition, not just per-hook-point (`before_submit` / `before_merge`).

If per-task step customization is ever needed, the `flow_overrides` field already exists in the task schema (currently unused). That would be the natural extension — override specific transition runs for a single task without creating a whole new flow.

## Open Questions

- Should `process_orchestrator_hooks` be removed in one go, or gradually (first make flows complete, then deprecate hooks, then remove)?
- Does the HookManager do anything that `execute_steps` doesn't (evidence recording, status tracking)?
- Should `execute_steps` support per-step error policies (retry, reject, fail) rather than all-or-nothing?

## Possible Next Steps

- Immediate fix: add `rebase_on_base` to default flow's `provisional -> done` runs and add rebase error handling to `handle_agent_result_via_flow` — this fixes the acute bug
- Then: audit all flows to ensure their runs lists are complete vs what hooks would have provided
- Then: stop writing hooks to task records at creation time
- Then: remove `process_orchestrator_hooks` and HookManager
