---
**Processed:** 2026-02-18
**Mode:** human-guided
**Actions taken:**
- Verified core implementation is complete: flow.py, default.yaml, scheduler integration, CLAIMABLE_AGENT_ROLES deleted
- Created follow-up drafts for remaining work: hook manager removal, project flow deployment, task overrides + condition persistence
- Created architecture document: docs/flows-architecture.md (referenced from CLAUDE.md)
**Outstanding items:** none (follow-up work tracked in separate drafts)
---

# Flows as the Single Integration Path

**Status:** Complete
**Captured:** 2026-02-16
**Related:** Draft 10 (Declarative Task Flows), Draft 10 (server-side)

## Raw

> Read 10-2026-02-16-declarative-task-flows. This is the ideal way we want to work. There needs to be at least one flow set up for a local project. This should be populated in the init function. Tasks should be able to override parts of the flow it is attached to, but in most cases we should default to the flows that are defined. I don't know how claimable_agent_roles fits into this. It feels like there are multiple overlapping ways of doing the same thing, when there should be a preferred approach. Additionally, these extra roles should be patched into the core state machine that is used for a task, rather than being separate boltons with different logic.

## Idea

Draft 17 proposes declarative flows. This draft goes further: flows should be **the only way** tasks move through the system.

### The model: conditional state machine

A flow is a state machine where transitions have **conditions** (gates that must pass) and **actions** (things that run during the transition). A task is always in exactly one state. There is no nested state tracking.

```
incoming ──[agent: implementer]──→ claimed ──[runs: rebase, tests, pr]──→ provisional ──[gatekeeper_pass, human_approval]──→ done
               ↑                                                               │
               └──────────────────────[condition_failed]───────────────────────┘
```

Each transition has:
- **agent** — which agent role performs the work in this state
- **runs** — scripts/hooks that execute during the transition
- **conditions** — gates that must pass before the transition completes
- **on_fail** — where to go if a condition fails

### Conditions can be programmatic or LLM agents

A condition is anything that evaluates to pass/fail. Two types:

**Programmatic conditions** — deterministic checks implemented as scripts or functions. Fast, cheap, no LLM needed.
- `tests_pass` — run pytest, exit code determines pass/fail
- `pr_exists` — check that a PR was created and is open
- `no_merge_conflicts` — check git status
- `diff_under_limit` — reject if diff exceeds N lines

**Agent conditions** — an LLM agent evaluates the task against criteria. Slower, costs tokens, but can reason about whether acceptance criteria are actually met.
- `gatekeeper_review` — LLM reads the diff + task description, judges whether the work is complete
- `architecture_review` — LLM checks for design pattern violations
- `security_review` — LLM scans for common vulnerabilities

Both types are declared the same way in the flow — the system handles the dispatch differently based on the condition's `type`:

```yaml
name: implement-review-merge

transitions:
  "incoming -> claimed":
    agent: implementer

  "claimed -> provisional":
    runs: [rebase_on_main, run_tests, create_pr]

  "provisional -> done":
    conditions:
      - name: tests_pass
        type: script
        script: run-tests
        on_fail: incoming

      - name: gatekeeper_review
        type: agent
        agent: sanity-check-gatekeeper
        on_fail: incoming

      - name: human_approval
        type: manual
    runs: [merge_pr]
```

Conditions are evaluated in order. If a programmatic condition fails, the agent condition never runs (no point wasting tokens if tests fail). If the agent condition fails, the task goes back to incoming before reaching human approval.

### What this replaces

All of the following collapse into flow definitions:

- `CLAIMABLE_AGENT_ROLES` — the flow declares which agent handles each state
- `AGENT_TASK_ROLE` — the flow maps agents to transitions
- `hooks` field on tasks — runs/conditions on transitions
- `task_types` in config.yaml — different flows for different task types
- Gatekeeper polling logic — an agent condition on the `provisional → done` transition
- `before_submit` / `before_merge` hook points — runs on specific transitions

### Default flows from `octopoid init`

`octopoid init` generates two flow files:

**Standalone task flow** — for tasks that own their own branch and PR:

```yaml
# .octopoid/flows/default.yaml
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

**Project flow** — for projects that coordinate multiple child tasks on a shared branch:

```yaml
# .octopoid/flows/project.yaml
name: project
description: Multi-task project with shared branch

# Flow applied to child tasks within this project.
# Children don't create PRs — the project creates one PR at the end.
child_flow:
  transitions:
    "incoming -> claimed":
      agent: implementer

    "claimed -> done":
      runs: [rebase_on_main, run_tests]
      # No create_pr — children commit to the shared branch

# Flow for the project itself, after all children complete.
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

A project declares which flow its child tasks use via `child_flow`. This is how project tasks skip PR creation — it's not a special case in the scheduler, it's just a different flow. The project itself has its own transitions that run after all children complete.

**Branch handling:** The project creates a feature branch at activation time. Each child task must rebase onto the project branch before starting work — not onto `main`. This ensures children build on each other's work. The `rebase_on_main` action in the child flow should actually rebase onto the project's branch (resolved from the task's project_id). Standalone tasks (no project) rebase onto main as before.

Users add gatekeeper review by adding a condition — no new agent wiring, no scheduler changes, no special-case code.

### Task overrides

Tasks default to the flow assigned by their project (or the installation default). Tasks can override specific transitions:

```yaml
id: TASK-abc123
flow: default
flow_overrides:
  "provisional -> done":
    conditions:
      - name: human_approval
        type: manual
        skip: true  # auto-merge, no human gate
```

## Context

This came up while reviewing the sanity-check-gatekeeper implementation. The agent was built, the scripts work, the prompt is good — but it's not actually plugged into anything because:
- `CLAIMABLE_AGENT_ROLES` doesn't include `gatekeeper`
- The gatekeeper has separate polling logic in the scheduler
- There's no way to declare "after provisional, run gatekeeper review"
- The state machine transitions are hardcoded, not configurable

The same pattern repeats: every new capability (gatekeeper, breakdown, project tasks) gets bolted on with its own special-case logic in the scheduler, rather than being expressed as a condition on a transition that the core state machine evaluates.

### Flow validation ("compilation")

Flows should be validated before a task enters the queue — a pseudo-compilation step that catches misconfigurations early rather than mid-execution.

When a task is created (or when a flow file is saved), validate:
- All referenced agents exist in `agents.yaml` and their directories are present
- All referenced scripts exist in the agent's `scripts/` directory
- Condition types are valid (`script`, `agent`, `manual`)
- Transition targets are valid states in the flow
- `on_fail` targets exist
- No unreachable states, no dead ends (except terminal states like `done`, `failed`)

This runs at task creation time. If validation fails, the task is rejected with a clear error: "Flow 'implement-review-merge' references agent 'sanity-check-gatekeeper' but no agent directory exists at .octopoid/agents/sanity-check-gatekeeper/".

Could also run on `octopoid init` and as a CLI command (`octopoid flow validate`) for authoring flows.

## Open Questions

- How does the scheduler discover which flow stage a task is at? Derived from the queue, or stored on the task?
- How do flows interact with projects? Does a project define its flow, or do individual tasks?
- ~~What's the migration path? Can we run the current hardcoded logic alongside flows during transition?~~ **Decided: no migration path. Delete the old code. Flows replace everything, not a second path.**
- Should condition results be persisted on the task? (e.g. `conditions_passed: [tests_pass, gatekeeper_review]` so we know what's been evaluated)
- How do agent conditions get their context? Does the flow declare what to feed them (diff, task description, check results)?

## Possible Next Steps

- Audit all current implicit state machines: trace every queue transition and what triggers it
- Define 2-3 concrete flows that represent current behaviour (implement, breakdown, hotfix)
- Prototype: make `octopoid init` generate a default flow file
- Refactor the scheduler to read flow definitions instead of hardcoded role mappings
- Migrate gatekeeper from agent condition prototype
