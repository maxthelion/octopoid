# Declarative Flows

Flows define how tasks move through the system as conditional state machines. They replace hardcoded logic with declarative YAML configurations.

## Overview

A flow is a state machine where transitions have:
- **Conditions** (gates that must pass)
- **Actions** (scripts that run during the transition)
- **Agents** (who handles work in each state)

## Flow File Format

Flows are YAML files stored in `.octopoid/flows/`.

### Example: Default Flow

```yaml
name: default
description: Standard implementation with review

transitions:
  "incoming -> claimed":
    agent: implementer

  "claimed -> provisional":
    runs: [rebase_on_main, run_tests, create_pr]

  "provisional -> done":
    conditions:
      - name: human_approval
        type: manual
    runs: [merge_pr]
```

### Example: Project Flow

For projects that coordinate multiple child tasks on a shared branch:

```yaml
name: project
description: Multi-task project with shared branch

# Flow applied to child tasks within this project
child_flow:
  transitions:
    "incoming -> claimed":
      agent: implementer

    "claimed -> done":
      runs: [rebase_on_project_branch, run_tests]
      # No create_pr — children commit to the shared branch

# Flow for the project itself, after all children complete
transitions:
  "children_complete -> provisional":
    runs: [create_pr]
    conditions:
      - name: all_tests_pass
        type: script
        script: run-tests

  "provisional -> done":
    conditions:
      - name: human_approval
        type: manual
    runs: [merge_pr]
```

## Transition Format

Each transition is declared as `"state1 -> state2"` with optional configuration:

```yaml
"from_state -> to_state":
  agent: role_name        # Agent that handles work in from_state
  runs: [script1, script2]  # Scripts to run during transition
  conditions:              # Gates that must pass
    - name: condition_name
      type: script|agent|manual
      # ... condition-specific fields
```

## Condition Types

### Script Conditions

Deterministic checks implemented as scripts. Fast, cheap, no LLM needed.

```yaml
conditions:
  - name: tests_pass
    type: script
    script: run-tests
    on_fail: incoming  # Where to go if check fails
```

The script must exit with code 0 to pass.

### Agent Conditions

An LLM agent evaluates the task against criteria. Slower, costs tokens, but can reason about acceptance criteria.

```yaml
conditions:
  - name: gatekeeper_review
    type: agent
    agent: sanity-check-gatekeeper
    on_fail: incoming
```

### Manual Conditions

Human approval required. The task waits in the current state until manually approved.

```yaml
conditions:
  - name: human_approval
    type: manual
```

## Task Overrides

Tasks default to the project's flow (or the installation default). Tasks can override specific transitions:

```yaml
# In task frontmatter
id: TASK-abc123
flow: default
flow_overrides:
  "provisional -> done":
    conditions:
      - name: human_approval
        type: manual
        skip: true  # Auto-merge, no human gate
```

## Flow Validation

Flows are validated when:
1. `octopoid init` generates default flows
2. A task is created (validates the task's flow)
3. Manually via CLI: `octopoid flow validate <flow-name>`

Validation checks:
- All referenced agents exist in `agents.yaml`
- All referenced scripts exist in the agent's `scripts/` directory
- Condition types are valid (`script`, `agent`, `manual`)
- Transition targets are valid states
- `on_fail` targets exist
- No unreachable states (except terminal states like `done`, `failed`)

## Creating Flows

### Default Flows from `octopoid init`

`octopoid init` generates two flow files:

1. **default.yaml** - Standard implementation flow
   - incoming → claimed: implementer claims
   - claimed → provisional: runs rebase, tests, creates PR
   - provisional → done: human approval, merges PR

2. **project.yaml** - Multi-task project flow
   - Child tasks skip PR creation, commit to shared branch
   - Project creates one PR after all children complete

### Custom Flows

Create new flows in `.octopoid/flows/`:

1. Create `my-flow.yaml`
2. Define transitions
3. Validate: `octopoid flow validate my-flow`
4. Use in tasks: set `flow: my-flow` in task frontmatter

## Migration from Previous System

Flows replace the following mechanisms:

| Old | New |
|-----|-----|
| `CLAIMABLE_AGENT_ROLES` | Flow declares which agent handles each state |
| `AGENT_TASK_ROLE` | Flow maps agents to transitions |
| `hooks` field on tasks | `runs` and `conditions` on transitions |
| `task_types` in config.yaml | Different flows for different task types |
| Gatekeeper polling logic | Agent condition on `provisional → done` transition |
| `before_submit` / `before_merge` hook points | `runs` on specific transitions |

## Architecture Notes

### Condition Evaluation Order

Conditions are evaluated in the order declared. This allows optimization:
- Put cheap programmatic checks first
- Put expensive agent checks after
- Put manual gates last

If a programmatic condition fails, agent conditions never run (saves tokens).

### Branch Handling

Projects create a feature branch at activation. Child tasks rebase onto the project branch (not main), so they build on each other's work. The `rebase_on_main` action in child flows actually rebases onto the project's branch (resolved from `project_id`).

Standalone tasks (no project) rebase onto main as before.

### State Machine Integration

The scheduler reads the flow to determine:
1. What agent can claim a task (by checking transitions from current state)
2. What actions to run during a transition
3. What conditions must pass before completing a transition
4. Where to send a task if a condition fails

This replaces all hardcoded state transitions in the scheduler.
