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

A file (or set of files) like `project-management/system-spec.yaml` or `project-management/behaviours/` that describes the system in terms of **behavioural invariants**:

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

## The key invariant: a centralised store of invariants must exist

The most important thing this draft establishes — and the thing not fully covered by the follow-on drafts — is the **meta-invariant**: there must be a centralised, canonical store of system invariants (`project-management/system-spec.yaml`). Every other improvement depends on this existing.

Without a centralised store:
- Invariants live in drafts, scattered across `project-management/drafts/`
- When a draft is archived, its invariants are archived with it
- No agent can answer "what are all the things this system guarantees?"
- Improvements get applied locally because there's no list of system-wide properties to check against

The store is what makes everything else work. Draft #181 bootstraps it with the first entries. Draft #182 defines how invariants flow into it. But the existence of the store itself is this draft's contribution.

## How invariants enter the store

Every draft should have its invariants extracted — either when it is enqueued into work (`/enqueue`) or when it is processed (`/process-draft`). If a draft doesn't have invariants, we should discuss what they should be. A draft that proposes a change to system behaviour without stating what should be true afterwards is incomplete.

The lifecycle:

1. **Draft captured** — idea described, invariants stated in `## Invariants` section
2. **Draft enqueued** — `/enqueue` checks that tasks collectively cover the invariants
3. **Tasks implemented** — partial progress is fine, gatekeeper approves at task level
4. **Draft processed** — `/process-draft` checks whether invariants are met in the code
5. **Invariants graduate** — met invariants move from the draft to `project-management/system-spec.yaml`
6. **Draft archived** — only after invariants are in the centralised store

This is refined in detail in draft #182.

## How it fits into the agent workflow

*Note: the original version of this section proposed that implementers update the spec on every change and the gatekeeper checks it was touched. Draft #182 refines this — see below.*

The implementer is typically a Sonnet model following mechanical acceptance criteria. It doesn't have the broader context to know which system invariants its change might affect. Asking it to update the spec on every commit is unreliable.

Instead, invariant management lives at the **draft level**, not the task level:

1. **Interactive Claude (Opus)**: writes drafts with invariants, creates tasks that collectively cover them. The human's intent is captured in the draft, translated into mechanical work in tasks.

2. **Implementer (Sonnet)**: follows task acceptance criteria. Doesn't need to know about invariants — that's handled upstream (draft) and downstream (process-draft). Partial progress toward an invariant is fine.

3. **Gatekeeper**: checks task completion, not invariant satisfaction. A task that makes partial progress toward a draft's invariant should be approved if it meets its own criteria.

4. **`/process-draft`**: checks whether the draft's invariants are actually met in the code. If not, the draft stays open and more work gets scheduled. This is the accountability mechanism.

5. **Codebase analyst**: periodically reviews `project-management/system-spec.yaml` for staleness — invariants whose tests don't exist, invariants that contradict the code, invariants with `test: null`.

6. **Testing analyst**: derives integration tests from untested invariants in the spec. This is the "QA from intent" loop — the spec says what should be true, the analyst writes the test that verifies it.

## How tests get derived from intent

The spec becomes the source of truth for what tests should exist. The testing analyst:

1. Reads `project-management/system-spec.yaml`
2. Finds behaviours where `test: null`
3. Writes integration tests that verify the behavioural description
4. Updates the spec to link the new test

This inverts the normal flow. Instead of "write code, then maybe write a test," it's "the behaviour exists in the spec, therefore a test must exist." The spec is the accountability mechanism.

## Refined by later drafts

- **Draft #181** — Bootstraps `project-management/system-spec.yaml` with the first concrete invariants (self-correcting failure, step verification, worktree preservation, claim limits). Answers the "how to bootstrap?" question.
- **Draft #182** — Defines the invariant-driven task pipeline: how invariants flow from drafts through tasks to the system spec. Refines the agent workflow section above. Identifies the process gap where intent gets lost in translation from draft → task → implementation.

## Open Questions

- What granularity? Too fine-grained and the spec becomes noisy. Too coarse and it doesn't catch regressions. Probably: one entry per user-visible behaviour or system invariant, not per function.
- YAML or markdown? YAML is machine-parseable (analysts can query it). Markdown is more readable. Could do both — YAML source of truth, markdown rendered view.
- Should the spec be per-module or global? A single file gets unwieldy. A directory (`project-management/behaviours/task-lifecycle.yaml`, `project-management/behaviours/flow-system.yaml`) might scale better.
- How to handle contradictions? If a new task changes behaviour that contradicts an existing spec entry, the agent must update the entry — not just add a new one. `/process-draft` should flag if an invariant was removed without explanation.
- How do we handle invariants that are aspirational vs enforced? Mark them differently? (e.g. `status: enforced` vs `status: aspirational`)
- What if a draft has no obvious invariants? Some drafts are pure refactoring or tooling changes. Do they still need invariants, or is "no invariant" a valid answer for some categories of work?

## Possible Next Steps

- Bootstrap `project-management/system-spec.yaml` with initial invariants (draft #181)
- Add `## Invariants` section to the `/draft-idea` template
- Update `/process-draft` to check invariant satisfaction before archiving
- Update `/enqueue` to flag when tasks don't fully cover draft invariants
- Retroactively add invariants to open drafts (#170, #175, #176, #180)
- Have the codebase analyst review the spec periodically for staleness
