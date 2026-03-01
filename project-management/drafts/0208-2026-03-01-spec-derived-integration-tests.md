# Spec-derived integration tests: every capability gets happy-path and failure-mode coverage

**Captured:** 2026-03-01

## Raw

> I'm trying to distill what the system does a bit further. Some of the points in the spec feel a bit disjointed and perhaps too granular. Here is my current version of what we do:
> * Drafts describe changes to the system
> * Work is done via tasks and projects
> * Work progresses through defined stages
> * A scheduler helps work through the system
> * It makes claims on available work and assigns implementers
> * After work is completed, it checks status and gets it ready for checking
> * Work marked as completed by implementers is checked by gatekeepers
> * Rejections from gatekeepers sends work back to be redone
> * Work that is successfully completed is merged into the codebase
> * The intent behind the work is canonised after it is done
> * Background agents fight against increasing complexity and regressions
> * Testing is done alongside implementation particularly against intent
> * Failure is surfaced early and noisily and automatically heals
> * Agents are assigned work primarily through messages
> * Agents are stateless and mostly fungible
> * Work is specified and broken down by higher classes of LLM
> * Visibility of work is via agent scripts and a TUI dashboard
> * The source of truth for the status and claims on work is in a server component
> * Work is done in git worktrees, that are created by the scheduler
> * Rebasing happens multiple times in the task flow as a manual step
> * Merging is handled by the scheduler after checks such as CI
> * Failures in merging are sent to agents to fix
> * Work is pull-based, guard stop agents creating too much.
>
> The issue I am thinking about at the moment is that only some of this is tested with integration tests. Not all of it is even built. For each bit of that, we really need integration tests which cover both the happy path, and the failure modes. What's more, we need to make sure that integration tests are a key part of developing new parts of the system. But what I've also been trying to get at is that the integration tests shouldn't be tacked on to work in an adhoc way, but should in some way be derived from the canonical idea of the system that we've built up. The test analyser should be looking at where there are gaps from this perspective.

## Idea

The system spec (v2) describes what the system is. Each capability should map to integration tests that prove it works — both the happy path and the failure modes. Today, tests exist for some capabilities but were written ad-hoc, not derived from the spec. The gap is structural: there's no mechanism that connects "the spec says X" to "test Y proves X".

Three things need to be true:

1. **Every spec capability has a test mapping.** For each of the ~22 capabilities above, there should be explicit integration tests covering the happy path and at least one failure mode. The spec's `tested` boolean should flip to `true` only when a specific test exists and passes.

2. **New capabilities get tests as part of implementation, not after.** When a new capability is built, the task should include writing the integration test. The spec invariant and the test are created together — the test is derived from the invariant, not invented separately.

3. **The test analyser uses the spec to find gaps.** Rather than scanning code coverage or looking at recent PRs, the testing analyst should read the spec, find invariants where `tested: false`, and propose integration tests for them. The spec is the test backlog.

## The 22 capabilities as a test matrix

Each line below is a capability that needs happy-path + failure-mode integration tests:

| # | Capability | Happy path | Failure mode | Tested? |
|---|-----------|-----------|-------------|---------|
| 1 | Drafts describe changes | Create draft via SDK, verify stored | Create with missing fields | Partial |
| 2 | Work via tasks and projects | Create task, verify in incoming | Create with invalid flow | Yes |
| 3 | Defined stages (flows) | Task transitions through flow | Invalid transition rejected | Yes |
| 4 | Scheduler drives work | Tick claims and dispatches | Tick with no available work | No |
| 5 | Claims and assigns implementers | Claim task, verify claimed_by | Claim already-claimed task | Yes |
| 6 | Checks status post-implementation | Submit result, task moves to provisional | Submit with bad outcome | Partial |
| 7 | Gatekeeper reviews | Approve moves to done | Reject sends back | No* |
| 8 | Rejections loop back | Reject increments count, requeues | Max rejections reached | No |
| 9 | Successful work merged | Approve triggers merge_pr step | Merge conflict on rebase | No* |
| 10 | Intent canonised after merge | update_changelog step runs | Changelog step fails | No |
| 11 | Background agents vs complexity | Analyst runs, produces draft | Guard prevents pileup | No |
| 12 | Testing alongside implementation | Test step in flow | Tests fail, task rejected | No |
| 13 | Failure surfaces early, auto-heals | needs_intervention triggers fixer | Fixer fails, circuit breaker | No |
| 14 | Agents assigned via messages | Message posted on dispatch | Message delivery fails | Partial |
| 15 | Agents stateless and fungible | Different agent claims same task type | Agent crash, lease expires, reclaim | Yes |
| 16 | Work specified by higher-class LLM | Task created with content | Content missing | Yes |
| 17 | Visibility via dashboard/TUI | Queue status returns data | Server unreachable | Partial |
| 18 | Server is source of truth | All state reads from server | Server returns stale data | Yes |
| 19 | Work in git worktrees | Worktree created on claim | Worktree creation fails | No |
| 20 | Rebasing in task flow | Rebase step succeeds | Rebase conflict | No |
| 21 | Merging after CI checks | merge_pr step after approve | CI fails, task rejected | No |
| 22 | Pull-based, guards limit volume | Guard script prevents dispatch | Guard incorrectly blocks | No |

\* Row 7/9: The bug found today (task 2b09a4db) — gatekeeper approve runs steps but never transitions — would have been caught by row 7's happy-path test.

## Invariants

- **spec-drives-test-backlog**: The testing analyst reads the system spec to identify untested capabilities. Its gap analysis is "which invariants have `tested: false`", not "which lines of code lack coverage".

- **capability-tests-cover-both-paths**: Each spec capability has at least two integration tests: one for the happy path, one for a failure mode. The failure mode test is as important as the happy path — most bugs hide in error handling.

- **new-capabilities-include-tests**: When a new capability is implemented via a task, the acceptance criteria include writing the integration test that will flip the invariant's `tested` field to `true`. The test is not a follow-up task — it's part of the implementation.

## Context

Follows from draft 179 (intent-driven development), draft 204 (spec completion + test audit), and draft 087 (testing analyst agent). The v2 system spec now has the `implemented`/`tested` boolean model that makes this tractable — each invariant explicitly tracks whether a test exists. The 22-capability distillation above is the user's current mental model of what the system does, which is more narrative and less granular than the spec's 73 invariants.

## Related Drafts

- **179** — Intent-driven development: the original idea that tests should derive from a canonical system description
- **204** — Spec completion and test audit: the operational plan for filling the spec and auditing coverage
- **087** — Testing analyst agent: the agent that should be doing this gap analysis

## Open Questions

- Should the 22 capabilities become a separate "capabilities" layer in the spec, sitting above the detailed invariants? Or should they map 1:1 to spec sections?
- How does the test analyser agent actually read the spec? Does it parse the YAML directly, or does it use the built JSON?
- Should capability tests be tagged/grouped so `pytest -m capability` runs them as a suite?

## Possible Next Steps

- Map each of the 22 capabilities to specific spec invariants (many are already there, some need regrouping)
- Write the first batch of missing integration tests for the highest-value gaps (rows 7, 9, 13 — the gatekeeper and failure-handling paths)
- Update the testing analyst prompt to read the spec and propose tests for `tested: false` invariants
