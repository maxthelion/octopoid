# Declarative Flows — Architecture

Flows define how tasks move through the system as conditional state machines. They are **the** mechanism for task transitions — there are no hardcoded state transitions in the scheduler.

## How It Works

A flow is a YAML file in `.octopoid/flows/` that declares states, transitions, conditions (gates), and actions (runs). A task is always in exactly one state (mapped to its queue: `incoming`, `claimed`, `provisional`, `done`, `failed`).

```
incoming ──[agent: implementer]──→ claimed ──[runs: push, test, pr]──→ provisional ──[agent: gatekeeper]──→ done
               ↑                                                            │
               └───────────────────[on_fail: incoming]──────────────────────┘
```

### Current Default Flow

This is the live flow in `.octopoid/flows/default.yaml`:

```yaml
name: default
description: Standard implementation with review

transitions:
  "incoming -> claimed":
    agent: implementer

  "claimed -> provisional":
    runs: [push_branch, run_tests, create_pr, submit_to_server]

  "provisional -> done":
    conditions:
      - name: gatekeeper_review
        type: agent
        agent: gatekeeper
        on_fail: incoming
    runs: [post_review_comment, merge_pr]
```

### What each part means

**`agent`** — which agent role handles work in the `from_state`. The scheduler checks this when deciding if an agent can claim a task.

**`runs`** — step functions (registered in `orchestrator/steps.py`) that execute during the transition. These are Python functions, not shell scripts. Current steps: `push_branch`, `run_tests`, `create_pr`, `submit_to_server`, `post_review_comment`, `merge_pr`.

**`conditions`** — gates evaluated in order before the transition completes. Three types:

| Type | How it works | Example |
|------|-------------|---------|
| `script` | Runs a script, exit code 0 = pass | `tests_pass` |
| `agent` | Spawns an LLM agent that returns approve/reject | `gatekeeper_review` |
| `manual` | Waits for human approval | `human_approval` |

**`on_fail`** — where to send the task if a condition fails. If the gatekeeper rejects, the task goes back to `incoming` for the implementer to fix.

## How the Scheduler Uses Flows

1. **Claiming tasks** (`evaluate_agent` → `guard_task_available`): The scheduler reads the agent's `claim_from` config (default: `incoming`, gatekeeper uses `provisional`) and claims the next matching task.

2. **Agent finishes** (`check_and_update_finished_agents`): When an agent's process exits, the scheduler reads its `result.json`.

3. **Flow dispatch** (`handle_agent_result_via_flow`):
   - Loads the flow for the task
   - Finds the transition matching the task's current state
   - If the result is `success` with `decision: approve`, runs the `runs` steps for that transition
   - If the result is `reject` or `failure`, follows the `on_fail` path

4. **Steps execute** (`orchestrator/steps.py`): Each step is a registered function. Steps receive `(task, result, task_dir)` and perform their action (push code, create PR, merge PR, etc).

## Key Files

| File | Purpose |
|------|---------|
| `.octopoid/flows/default.yaml` | The live flow definition |
| `orchestrator/flow.py` | Flow/Transition/Condition dataclasses, YAML parsing, validation |
| `orchestrator/steps.py` | Step functions registered via `@register_step` |
| `orchestrator/scheduler.py` | `handle_agent_result_via_flow()` — the core dispatch logic |

## Agents Are Pure Functions

Agents don't call the SDK or manage task state. They:
1. Get spawned by the scheduler with a worktree and task context
2. Do their work (implement code, review a diff, etc.)
3. Write `result.json` with `{status, decision, comment}` and exit

The scheduler reads the result and drives the flow. This means:
- Agents can't create circular dependencies
- All state transitions are visible in the flow YAML
- The scheduler is the single supervisor

## Condition Evaluation Order

Conditions are evaluated in declaration order. Put cheap checks first:
1. Programmatic checks (fast, free)
2. Agent checks (slow, costs tokens)
3. Manual gates (waits for human)

If a programmatic condition fails, agent conditions never run (saves tokens).

## Project Flows

Projects use a separate flow (`project.yaml`) that governs the project-level state machine,
alongside a `child_flow` that governs individual child tasks.

```yaml
name: project
description: Multi-task project with shared branch

child_flow:
  transitions:
    "incoming -> claimed":
      agent: implementer
    "claimed -> done":
      runs: [rebase_on_project_branch, run_tests]
      # No create_pr — children commit to the shared branch

transitions:
  "children_complete -> provisional":
    runs: [create_project_pr]
    conditions:
      - name: all_tests_pass
        type: script
        script: run-tests

  "provisional -> done":
    conditions:
      - name: human_approval
        type: manual
    runs: [merge_project_pr]
```

**How project flows work:**

1. **`check_project_completion()` (60s tick)**: When all child tasks reach `done`, the scheduler loads
   the project's flow, finds the `children_complete -> provisional` transition, evaluates any script
   conditions (e.g. `all_tests_pass` runs tests in the parent project directory), executes the step
   list (`create_project_pr`), then sets project status to `provisional`.

2. **`approve_project_via_flow(project_id)`**: Called when a human approves a project in `provisional`
   status. Finds the `provisional -> done` transition, runs its steps (`merge_project_pr`), and sets
   project status to `done`. Manual conditions require explicit approval through this function.

**Project-specific steps** (registered in `steps.py`):
- `create_project_pr` — creates a PR from the project's shared branch to the base branch; stores
  `pr_url`/`pr_number` on the project via `sdk.projects.update()`
- `merge_project_pr` — merges the project's PR via `gh pr merge`

## Not Yet Implemented

These features are designed but not yet built (tracked in separate drafts):

- **Task-level flow overrides** — tasks overriding specific transitions via `flow_overrides` (Draft 43)
- **Condition result persistence** — storing which conditions already passed on the task (Draft 43)
- **Hook manager removal** — the hook manager still runs alongside flows as a legacy path (Draft 41)
