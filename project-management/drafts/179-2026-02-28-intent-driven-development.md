# Intent-driven development: canonical system spec that agents maintain and tests derive from

**Captured:** 2026-02-28

## Raw

> I don't feel like we have a canonical idea of what the system is meant to be doing. I'd like to be able to derive QA and integration tests etc from that.

> With agentic development, the process is the prompt. All the discipline that used to live in team culture, PR reviews, and personal habits now needs to be encoded explicitly in the agent's instructions and the CI pipeline.

## Idea

The fundamental problem with agentic development is that intent evaporates. A human says "add notification snoozing", an agent produces working code, and the *why* — the behavioural contract that the system now has with its users — lives nowhere durable. Tests verify implementation details, not intent. If someone later changes the snooze logic, nothing checks whether "users can snooze for 24 hours" still holds unless there happens to be a test named after it.

The fix is a **canonical system spec** — a living document that describes what the system is supposed to do, maintained by agents as a side-effect of every change, and used to derive QA, integration tests, and regression suites.

## Where we currently fall short

### 1. No canonical description of system behaviour

CLAUDE.md describes *how to work* (process rules, git hygiene, testing philosophy). It doesn't describe *what the system does*. There's no document an agent can read to answer "what are the behavioural guarantees of octopoid?" Agents make changes in a vacuum — they know the task, but not the system.

### 2. Tasks describe work, not intent

A task says "add rate limiting to API" with acceptance criteria like "returns 429 when exceeded." But this is scoped to the task. Once it's done, the intent ("API endpoints are rate-limited") doesn't get recorded anywhere permanent. The task goes to `done` and the knowledge is effectively archived.

### 3. Tests are implementation-derived, not intent-derived

Our tests verify that code works: "this function returns the right value", "this API call returns 200." They don't verify system-level intent: "the scheduler never claims more tasks than the pool allows", "a failed task always becomes visible to either a fixer or a human within 60 seconds." When implementation changes, the tests change with it, and the intent they used to encode silently disappears.

### 4. Gatekeeper checks task completion, not system coherence

The gatekeeper asks "did the agent do what the task said?" It doesn't ask "does the system still behave the way it's supposed to?" A change could perfectly satisfy its task while breaking an invariant that no task mentioned.

### 5. Acceptance criteria don't accumulate

Each task has its own acceptance criteria. They're checked at completion time and then forgotten. There's no mechanism where completed acceptance criteria become permanent system assertions. The sum of all completed tasks should describe the system — but it doesn't, because the criteria are per-task and ephemeral.

### 6. No traceability from code to intent

If you look at a function in the codebase, you can find the commit that created it and the PR it came from. But you can't easily answer "what user-facing behaviour depends on this function?" The link from code → commit → branch → task → intent exists but isn't queryable.

### 7. Agent prompts don't enforce hygiene structurally

The implementer prompt doesn't say "update the system spec" or "ensure your change has a corresponding behavioural test." The gatekeeper doesn't check for it. The flow steps don't include it. So it doesn't happen.

## What a canonical system spec looks like

A file (or set of files) like `docs/system-spec.yaml` or `docs/behaviours/` that describes the system in terms of **behavioural invariants**:

```yaml
behaviours:
  task-lifecycle:
    - id: task-claim-limit
      description: "The scheduler never claims more tasks than max_claimed allows"
      added_by: task-76ce7e3f
      test: tests/integration/test_backpressure.py::test_blocks_at_capacity

    - id: task-failure-visibility
      description: "A failed task becomes visible to either a fixer agent or the human inbox within one scheduler tick"
      added_by: task-59d65398
      test: null  # needs test

    - id: worktree-preservation
      description: "When a task is requeued after an agent has worked on it, the existing worktree and commits are preserved"
      added_by: task-76ce7e3f
      test: tests/test_create_task_worktree_integration.py::test_worktree_survives_requeue
```

Each behaviour:
- Has a human-readable description of the invariant
- Links to the task that introduced it
- Links to the test that verifies it (or marks it as untested)
- Is read by agents before making changes (so they know what to preserve)
- Is updated by agents after making changes (so new behaviours are captured)

## How it fits into the agent workflow

1. **Implementer**: reads the spec before starting. After completing work, appends any new behaviours introduced by the change. If the change modifies an existing behaviour, updates the description.

2. **Gatekeeper**: checks that the spec was updated if the diff introduces new behaviour. Checks that existing behaviours still have passing tests. Flags untested behaviours.

3. **Codebase analyst**: periodically reviews the spec for staleness — behaviours whose tests no longer exist, behaviours that contradict the code, behaviours that have no test.

4. **Testing analyst**: derives integration tests from untested behaviours in the spec. This is the "QA from intent" loop — the spec says what should be true, the analyst writes the test that verifies it.

5. **Flow step**: a `validate_spec` step in `claimed -> provisional` that checks the spec file was touched if the diff is non-trivial.

## How tests get derived from intent

The spec becomes the source of truth for what tests should exist. The testing analyst:

1. Reads `docs/system-spec.yaml`
2. Finds behaviours where `test: null`
3. Writes integration tests that verify the behavioural description
4. Updates the spec to link the new test

This inverts the normal flow. Instead of "write code, then maybe write a test," it's "the behaviour exists in the spec, therefore a test must exist." The spec is the accountability mechanism.

## Open Questions

- What granularity? Too fine-grained and the spec becomes noisy. Too coarse and it doesn't catch regressions. Probably: one entry per user-visible behaviour or system invariant, not per function.
- YAML or markdown? YAML is machine-parseable (analysts can query it). Markdown is more readable. Could do both — YAML source of truth, markdown rendered view.
- How to bootstrap? We can't write the full spec from scratch. Start with the behaviours we already have tests for, then grow organically as tasks complete.
- Should the spec be per-module or global? A single file gets unwieldy. A directory (`docs/behaviours/task-lifecycle.yaml`, `docs/behaviours/flow-system.yaml`) might scale better.
- How to handle contradictions? If a new task changes behaviour that contradicts an existing spec entry, the agent must update the entry — not just add a new one. The gatekeeper should flag if a spec entry was removed without explanation.

## Possible Next Steps

- Bootstrap a minimal spec with 10-15 behaviours from existing tests and task descriptions
- Update the implementer prompt to read and update the spec
- Update the gatekeeper prompt to check spec was touched
- Update the testing analyst to derive tests from untested spec entries
- Add a `validate_spec` flow step
