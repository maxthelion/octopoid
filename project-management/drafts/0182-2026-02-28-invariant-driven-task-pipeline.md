# Invariant-driven task pipeline: drafts hold invariants, process-draft checks they are met

**Captured:** 2026-02-28
**Builds on:** Draft #179 (intent-driven development), Draft #181 (bootstrap system spec)

## Raw

> The interactive claude writes the draft and the tasks. They describe the work mechanically so that it can be implemented. The implementer doesn't get a huge amount of leeway. They are often sonnet models, rather than opus in the interactive claude. It seems that a draft should have the invariants. The step of turning a draft into enqueued work might need to be codified more closely such that the work always fully covers the invariant, there are integration tests of the right type etc. We can schedule work related to a draft that only partly matches the invariant. The gatekeeper shouldn't necessarily reject it for that. What needs to be clear though is whether the invariant has actually been met or not. When we run process-draft to see whether it can be archived, we aren't currently checking that the invariant has been met.

## The process gap

There's a pipeline for turning ideas into working code:

```
Human intent
  → Interactive Claude (Opus) writes draft
    → Interactive Claude creates tasks from draft (/enqueue)
      → Implementer (Sonnet) follows task acceptance criteria
        → Gatekeeper checks task completion
          → /process-draft checks if draft can be archived
```

The problem is where intent gets lost in this pipeline.

### Where the models sit

**Interactive Claude (Opus)** understands the broad intent. When the human says "the system should be self-correcting," Opus understands what that means systemically. It writes the draft, which captures the idea well.

**But then Opus writes the task.** And it translates the intent into mechanical acceptance criteria — "implement `fail_task()`, implement `request_intervention()`, add the fixer agent" — because that's what the implementer needs. The implementer is typically a Sonnet model. It doesn't have the broader context of the draft. It follows the criteria it's given.

**The implementer does the work correctly.** It builds `fail_task()` with intervention-first logic. It connects it to the circuit breaker. The mechanism works. The gatekeeper checks: did the agent do what the task said? Yes. Task done.

**But the invariant isn't met.** The intent was "every failure goes through intervention." The implementation only covers one failure path. The other failure path (`_handle_fail_outcome`) still routes directly to `failed`. Nobody checks this because:

1. The invariant lives in the draft, not in the task
2. The implementer never reads the draft — it reads the task file
3. The gatekeeper checks task completion, not invariant satisfaction
4. `/process-draft` checks for outstanding work items and open questions — not whether the invariant described in the draft actually holds in the code

### What happened with the self-healing system (concrete example)

1. **Draft #170** described the intent broadly: *"The flow becomes: [error] → requires-intervention → [fixed] → resume flow OR [can't fix] → failed."* This is a universal statement about all failures.

2. **The task** had acceptance criteria about the mechanism: implement `fail_task()`, implement the fixer agent, write intervention context. Mechanical, implementable, correct.

3. **The implementer** built all of it. Connected `fail_task()` to the circuit breaker (the specific failure mode that prompted the draft). Tests pass. PR created.

4. **The gatekeeper** verified the task was complete. It was.

5. **But** `_handle_fail_outcome()` — which handles the majority of agent failures — was never touched. It still routes directly to `failed`. The invariant "every failure goes through intervention" was never met.

6. **When `/process-draft` ran** (or would run) on draft #170, it would check: are the tasks done? Yes. Are there open questions? No. Archive it. But the invariant described in the draft doesn't actually hold in the code.

## What needs to change

### 1. Drafts should explicitly state their invariants

A draft isn't just a description of work to be done. It's a statement of intent. The invariant is the durable part — the thing that should be true in the system after all the work is complete.

```markdown
## Invariants

- `self-correcting-failure`: Every task failure goes through intervention
  before reaching the `failed` queue. Direct routing to `failed` without
  intervention is a bug.

- `fail-task-single-path`: All code paths that move a task to `failed`
  go through `fail_task()`. No direct `sdk.tasks.update(queue='failed')`
  calls outside `fail_task()` itself.
```

The invariant section is distinct from acceptance criteria. Acceptance criteria describe what a single task should do. Invariants describe what the system should guarantee after all tasks related to this draft are complete.

### 2. /enqueue should derive coverage from invariants

When turning a draft into tasks, the `/enqueue` step should check: do the tasks collectively cover the invariant? This doesn't mean every task has to fully satisfy the invariant — work can be incremental. But there should be a clear path from the set of tasks to invariant satisfaction.

For the self-healing example, `/enqueue` should have produced:
- Task 1: Implement `fail_task()` and `request_intervention()` *(mechanism)*
- Task 2: Implement fixer agent *(mechanism)*
- Task 3: **Audit all failure paths and route through `fail_task()`** *(coverage)*
- Task 4: **Write structural test: no direct `queue='failed'` outside `fail_task()`** *(enforcement)*

Tasks 3 and 4 are what was missing. They're the tasks that ensure the invariant is met, not just the mechanism built. Without them, the mechanism exists but isn't universally applied.

It's fine to schedule just Task 1 first. Partial progress toward the invariant is fine. What matters is that the draft tracks whether the invariant has been met.

### 3. The gatekeeper should not reject partial work

A task that implements `fail_task()` but doesn't update `_handle_fail_outcome()` isn't wrong — it's incomplete relative to the draft's invariant, but complete relative to its own acceptance criteria. The gatekeeper should approve it.

The invariant check lives at the draft level, not the task level. The gatekeeper checks tasks. The draft lifecycle checks invariants.

### 4. /process-draft should check invariant satisfaction

This is the critical missing piece. When `/process-draft` runs to determine whether a draft can be archived, it currently checks:
- Are all tasks done?
- Are there open questions?
- Is there outstanding work?

It should also check:
- **Are the invariants in the draft actually met in the code?**

For the self-healing invariant, this means:
- Search the codebase for direct `queue='failed'` calls outside `fail_task()`
- Run the structural test if it exists
- Check whether a test for the invariant exists in the test suite

If the invariant isn't met, the draft stays open — even if all its tasks are "done." The draft surfaces: "Tasks complete, but invariant `self-correcting-failure` is not satisfied. Remaining gap: `_handle_fail_outcome()` routes directly to `failed`."

This is the accountability loop. The draft stays open until reality matches intent.

### 5. Invariants graduate to the system spec

When a draft's invariant is finally met (code satisfies it, test enforces it), the invariant moves from the draft to `project-management/system-spec.yaml` (per draft #181). At that point:
- The draft can be archived
- The invariant is permanent — future changes are checked against it
- The gatekeeper can reference it when reviewing future tasks

The lifecycle is:

```
Draft (invariant stated)
  → Tasks (partial work toward invariant)
    → process-draft (invariant not yet met → stays open)
      → More tasks (remaining gaps)
        → process-draft (invariant met → archive draft)
          → system-spec.yaml (invariant is permanent)
```

## Changes needed to implement this

### Draft format
Add an `## Invariants` section to the draft template. Each invariant has:
- An ID (for referencing in system-spec.yaml)
- A human-readable description
- A testable assertion (grep pattern, test function, or query)

### /enqueue skill
When enqueuing from a draft that has invariants, prompt: "Does this task fully cover the invariant, or is it partial progress? If partial, what remains?" Track coverage explicitly.

### /process-draft skill
Add an invariant check step:
1. Read the draft's `## Invariants` section
2. For each invariant with a testable assertion, run the assertion
3. If any invariant is not met, the draft cannot be archived — list the gaps
4. If all invariants are met, check that they've been added to `project-management/system-spec.yaml`

### Draft-idea skill
When capturing a new idea, ask: "What should be true about the system after this is done?" The answer becomes the invariant.

### Gatekeeper prompt
No change needed. The gatekeeper checks task-level acceptance criteria, not draft-level invariants. This separation is intentional — partial work toward an invariant should be approved.

## Open Questions

- How do we handle invariants that require multiple drafts to satisfy? Does the invariant live in the first draft that stated it, or does it move to the system spec immediately as `status: aspirational`?
- Should the codebase analyst check invariants on a cadence, or only when `/process-draft` is called?
- How do we write testable assertions for invariants that are about system behaviour (e.g. "a failed task becomes visible to a fixer within one scheduler tick") vs structural properties (e.g. "no direct routing to failed outside fail_task")?
- What if an invariant is discovered to be wrong or too strict? Who can modify or remove it?

## Possible Next Steps

- Add `## Invariants` section to the `/draft-idea` template
- Update `/process-draft` to check invariants before allowing archive
- Update `/enqueue` to flag when tasks don't fully cover draft invariants
- Retroactively add invariants to open drafts (especially #170, #175, #176, #180)
- Bootstrap `project-management/system-spec.yaml` with the first few invariants (per draft #181)
