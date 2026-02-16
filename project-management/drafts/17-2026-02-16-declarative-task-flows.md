# Declarative Task Flows

**Status:** Idea
**Captured:** 2026-02-16

## Raw

> declarative flows that a task can follow. At the moment, I think we add the checks that a task will undergo into the task itself. It might be better if we have a flows directory in .orchestrator. In each flow would be scripts needed, the specific hooks for the flow etc. Tasks would be assigned a flow on creation. This might make it easier to reason about what is actually happening. Include your thoughts on this.

## Idea

Instead of embedding hooks and checks into each task at creation time, define reusable **flows** — named sequences of scripts, hooks, and lifecycle steps that a task follows from start to finish. Tasks get assigned a flow name (e.g. `flow: implement-and-review`) and the system looks up what that means at runtime.

```
.octopoid/flows/
  implement-and-review.yaml
  implement-only.yaml
  hotfix.yaml
  breakdown.yaml
```

Each flow declares the full lifecycle:

```yaml
# .octopoid/flows/implement-and-review.yaml
name: implement-and-review
description: Standard implementation with tests and gatekeeper review

scripts:
  - run-tests
  - submit-pr
  - finish
  - fail
  - record-progress

hooks:
  - name: run_tests
    type: agent
    required: true
  - name: create_pr
    type: agent
    required: true
  - name: rebase_on_main
    type: agent
    required: false

review:
  enabled: true
  checks:
    - scope
    - tests
    - debug-code

on_complete: provisional    # where does the task go when the agent finishes?
on_review_pass: done        # where after review passes?
on_review_fail: incoming    # where if review rejects?
```

Tasks just reference the flow:

```yaml
id: TASK-abc123
title: Fix the login bug
flow: implement-and-review
```

## Context

This came up during the scheduler refactor review. Currently, hooks are embedded in each task's JSON at creation time (the `hooks` field). This has several problems:

1. **Opaque** — you can't look at a task and immediately understand its full lifecycle without parsing the hooks array
2. **Repetitive** — every implement task gets the same hooks copied in
3. **Inflexible** — changing the standard flow means updating every future task creation site
4. **Scattered logic** — the scheduler has to interpret hooks at runtime, the gatekeeper has separate check config, the review system has its own rules. There's no single place that says "this is what happens to an implement task"

With agent directories (draft 9) already defining what an agent *is*, flows would complete the picture by defining what a task *does*. Agent directories = "who does the work", flows = "what steps the work follows".

## Thoughts

This is a good idea. Some observations:

**It's essentially a state machine definition.** Each flow defines: initial state, transitions, required actions at each stage, and terminal states. Right now this state machine is implicit — spread across the scheduler, queue_utils, hook processing, and gatekeeper coordination. Making it explicit and declarative would be a significant clarity win.

**It pairs naturally with agent directories.** An agent directory could declare which flows it supports (`flows: [implement-and-review, hotfix]`), and the system could validate that a task's flow is compatible with the agent type assigned to it.

**It would simplify task creation.** Instead of `sdk.tasks.create(hooks=[...], ...)`, you'd just say `flow: implement-and-review` and the system fills in the rest. The `/enqueue` skill gets simpler. Programmatic task creation gets simpler.

**Risk: over-engineering.** We currently have two real flows: "implement" (run tests → submit PR → gatekeeper review → done) and "lightweight" (just run and finish). A flows directory with YAML files might be more machinery than two patterns justify. The value increases if we expect more flow variants (hotfix, breakdown, review-only, etc).

**The review/gatekeeper integration is the hardest part.** Currently the gatekeeper system has its own orchestration (dispatch checks, collect results, decide pass/fail). A flow would need to either absorb that logic or hook into it cleanly. This is the area most likely to get messy.

## Open Questions

- How many distinct flows do we actually need today? If it's just 2-3, is YAML config worth it vs a simple enum?
- Should flows be project-level (`.octopoid/flows/`) or product-level (`packages/client/flows/`) with scaffolding, like agent directories?
- How does this interact with projects? Should a project be able to override the flow for its tasks?
- Does the flow replace the `hooks` field entirely, or do hooks become an implementation detail of the flow?
- How does the gatekeeper check system fit? Is review a first-class flow stage, or a separate concern?

## Possible Next Steps

- Audit the current implicit flows: trace what actually happens to an implement task vs a lightweight task vs a breakdown task from creation to completion
- Define 2-3 concrete flow YAML files based on existing behaviour
- Prototype: make `sdk.tasks.create(flow='implement-and-review')` expand into the current hooks
- Decide on the relationship between flows and agent directories
