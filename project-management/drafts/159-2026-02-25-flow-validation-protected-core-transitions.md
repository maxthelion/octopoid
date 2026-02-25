---
**Processed:** 2026-02-25
**Mode:** human-guided
**Actions taken:**
- Problem 1 (required steps omitted): Resolved via _inject_terminal_steps() in flow.py (draft #160, TASK-b8c2bf6c)
- Problem 4 (dashboard fragmentation): Matrix view implemented (draft #161, TASK-090746bb), polish enqueued (TASK-960c6203)
- Git problem: Phase 1 complete (draft #160) — changelog moved, rebase at submission, rejection message fixed
- Architectural principles extracted to docs/architecture-v2.md "Architectural Principles" section
- Added CLAUDE.md reference to architecture-v2.md
- Decision "D then B" captured — git robustness first, flow redesign later
**Outstanding items:** Option B (protected core lifecycle redesign) is future work — will get its own draft when the time comes. Problems 2, 3, 5 are all part of Option B.
---

# Flow validation and protected core transitions

**Status:** Complete
**Captured:** 2026-02-25

## Raw

> I think there might be an issue that some of the steps in a flow are actually necessary. Making a mistake in a yaml file can cause the whole process to break. Either we need validation of flows (that they include some of the necessary steps), or we inject some steps into the process automatically on the server. Also, this might mean that flows are not completely freeform state transitions — some steps might need to be defined entities that are referenced. Eg incoming, claimed, done, failed. It's only really in the provisional area that flows add customisation. We'd probably want the flows yaml to reference states and be able to add custom steps, but not mess around with the core.

## Idea

Flows currently allow completely freeform YAML definitions, which means a typo or missing step can silently break the entire task lifecycle. There are two related problems:

**1. Required steps can be omitted.** The `provisional -> done` transition needs `rebase_on_base` and `merge_pr` to actually land the work. If a flow YAML forgets these (as just happened with `fast.yaml` missing `rebase_on_base`), tasks get approved but never merged — or worse, merge without rebasing and hit conflicts.

**2. Core states should be protected.** The states `incoming`, `claimed`, `done`, and `failed` are load-bearing — the scheduler, SDK, and server all hardcode assumptions about them. Flows shouldn't be able to rename, remove, or redefine transitions between these core states. The customisation area is really between `claimed` and `done` (the "provisional" zone) where projects can add review steps, CI gates, staging deployments, etc.

**3. Transitions are not self-describing.** The current format uses `"state_a -> state_b"` as the transition key, which tells you the state change but not what the transition *does*. You have to read the conditions and runs blocks to understand. For example in the QA flow:

```yaml
"sanity_approved -> human_review":
  conditions:
    - name: qa_review
      type: agent
      agent: qa-gatekeeper
      on_fail: incoming
  runs: [post_review_comment]
```

The key `"sanity_approved -> human_review"` doesn't tell you this is the QA gatekeeper stage — you'd have to read into the conditions to discover that. State names end up describing the *outcome* of the previous transition ("sanity_approved") rather than the *current stage* ("qa_review"). The transition keys are just anonymous arrows between states with no semantic meaning about the work being done.

This makes flows hard to reason about at a glance, especially as they get more complex with multiple review stages.

**4. Per-flow tabs fragment the dashboard.** The kanban board shows one tab per flow, so work is scattered across tabs even though most columns are identical. If you have `default`, `qa`, and `fast` flows, incoming tasks appear in three separate tabs — you can't see all work at a glance without flicking between them. The flows mostly share the same core stages (incoming, claimed, done) and only differ in how many review gates sit between claimed and done. A unified board with shared core columns and flow-specific review stages inline would give a much better overview.

This reinforces point #2: if flows had a protected core (incoming → claimed → ... → done) with customisation only in the middle, the dashboard could render one unified board with the core columns always visible and the review stages varying per task.

**5. Projects don't fit the flow model but probably should.** Projects are like meta-tasks — a large body of work broken into sub-tasks. Their sub-tasks go through normal flows, but the project itself doesn't go through any lifecycle stages. Yet when a project nears completion (all sub-tasks done), it needs the same kind of landing process as a normal task: consolidate changes, create a PR, review, rebase, merge. Right now that's entirely manual. If flows had a well-defined core lifecycle, projects could reuse it — treating the project completion as a "task" that goes through the review/merge stages, just with a bigger scope.

## Context

This came up after diagnosing why a fast-flow task failed to merge. The `fast.yaml` flow was missing `rebase_on_base` in its `provisional -> done` runs because the fix commit (fc06ebe) only updated `default.yaml`. A validation layer would have caught this — either by requiring `rebase_on_base` + `merge_pr` on the terminal transition, or by injecting them automatically.

Related: draft #147 (consolidating flow runs and hooks) and draft #141 (flows not synced to server).

## Opinions from experience

Things we've become opinionated about through running the system:

- **Git worktrees work well, but must be detached HEAD.** Agents get a worktree pulled from origin, fully rebased for the work they need to do. Worktrees persist until work is done so it can be revisited quickly.
- **Implementer agents are pure functions.** They do work and report status. They shouldn't manage their own lifecycle (rebasing, merging, PR management). Keep them focused on the actual task.
- **The scheduler owns mechanical operations.** Rebasing and merging should be done by the scheduler, not agents. If these mechanical tasks fail, the problem should be thrown back to an agent to fix (e.g. resolve conflicts), not left for human intervention.
- **Failed should be an outlier outcome.** Most problems can be resolved within the system itself — reject back to incoming, retry with feedback, escalate to a different agent. Dumping to failed should be a last resort, not the default error path.
- **PRs may duplicate other mechanisms.** We use PRs, but it's unclear how much value they add when we already have task rejection with feedback, gatekeeper review, and commit history. PRs might be adding ceremony without proportional benefit.
- **Prefer mechanical solutions over LLMs for cost.** But don't shy away from automating LLM use when it reduces manual investigation work. The goal is less human intervention overall, and sometimes an LLM call is cheaper than a human context-switch.

## The git problem

Git operations (rebase and merge) are the single biggest source of failures in the system. The current approach patches individual cases — adding `rebase_on_base` to a flow, catching merge errors in the result handler — but there's no systemic solution.

The failure chain looks like this:
1. Agent works in a worktree, possibly for a while
2. Meanwhile other tasks merge to main, moving the target
3. When the task is approved, rebase hits conflicts
4. Task gets rejected back to incoming
5. A new agent picks it up, re-implements from scratch on a fresh base
6. By the time *that* finishes, main may have moved again

The waste is in step 5 — the original work was probably fine, it just needed rebasing. Re-implementing is expensive (LLM turns) and might hit the same conflict. What's missing:

- **Proactive rebasing during work.** The scheduler could periodically rebase long-running worktrees onto main, catching conflicts early rather than at merge time.
- **Conflict resolution as a distinct task type.** When rebase fails, instead of re-implementing the whole task, hand the conflict to an agent whose only job is to resolve the merge conflict in the existing worktree. Much cheaper than a full re-implementation.
- **Ordering awareness.** If two tasks touch the same files, serialize them rather than running in parallel. The second one should wait until the first merges.
- **Understanding the actual conflict rate.** We don't track how often rebases fail, what files conflict most, or whether the same files keep causing problems. Without data, we're guessing at solutions.

This ties back to the flow design: the core lifecycle should have git robustness built in, not bolted on per-flow. Every flow that ends in a merge needs the same rebase-resolve-merge machinery.

## Open Questions

- Should required steps be validated at load time (fail-fast) or injected automatically (fail-safe)?
- What is the minimal set of required steps on `provisional -> done`? (`rebase_on_base`, `merge_pr`? `push_branch`?)
- Should the server validate flows on registration, or should it be client-side only?
- How do we handle flows that intentionally skip merge (e.g. a "dry run" or "plan only" flow)?
- Should transitions have explicit names/descriptions, or should state names be rethought to describe the current stage rather than the previous outcome?
- Would a structure like `stages:` (with named stages that own their conditions/runs) be clearer than `transitions:` (anonymous arrows between states)?

## Options

**Option A: Patch the gaps.** Fix each problem individually — YAML validation for required steps, conflict resolver agent, proactive rebase, unified dashboard. Low risk, ships incrementally, but doesn't fix the structural issues.

**Option B: Protected core lifecycle.** Redesign flows so core states are fixed (`incoming → claimed → [review zone] → done`) and flows only define the review zone. Required mechanical steps injected automatically. Fixes validation, semantics, dashboard, and projects in one design. Significant refactor.

**Option C: Server owns the lifecycle.** Push lifecycle logic to the server. Flows become configuration validated on sync. Server enforces transitions and required steps. Single source of truth but most work, requires server changes.

**Option D: Simplify flows, invest in git robustness.** Minimal flow changes (inject required terminal steps, basic validation). Major investment in git: proactive rebasing, conflict resolver agent, file-level conflict tracking, task serialization. Targets the real pain point.

## Decision

**D then B.** Git failures are the immediate pain — fix those first. Then redesign flows with a protected core lifecycle, informed by what we learn. See draft #160 for the concrete git robustness plan.

## Possible Next Steps

- Draft #160: Concrete plan for git robustness (Option D)
- After D ships: revisit Option B (protected core lifecycle redesign)
- Define which states are "core" (immutable) vs "custom" (flow-defined)
- Define which steps are required on which transitions
- Explore alternative YAML structures that make flows self-describing
