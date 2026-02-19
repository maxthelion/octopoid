# Testing Progress Report: Draft 36 Revisited

**Status:** Idea
**Captured:** 2026-02-19

## Raw

> Read 36-2026-02-18-system-hardening-synthesis. Then explore the current state of testing. Are we better hardened in terms of end to end testing? Are the mocks for LLM agents and Git failure scenarios in play?

## Where Draft 36 Left Us (2026-02-18)

Draft 36 identified three goals: outside-in testing, pure-function agent model, and system hardening. It flagged the **most dangerous gap** as "no tests on the scheduler's critical path" and laid out a 4-tier plan.

## What's Changed Since Then (24 hours)

### Tier 1 (Blocking) — Mostly resolved

| Item | Draft 36 status | Current status |
|------|----------------|----------------|
| Fix gatekeeper | Claimed (TASK-b0a63d8b) | **Done** — merged, working |
| Fix `get_agents()` dropping gatekeeper | Not scheduled | **Done** — pool model (step 1) fixed this by switching to `agents:` dict with explicit `blueprint_name` |
| Log server error response bodies | Incoming (TASK-27adf598) | **Done** — merged |

### Tier 2 (Reliability) — Partially resolved

| Item | Draft 36 status | Current status |
|------|----------------|----------------|
| Pool model | Incoming, sequential | **Done** — all 4 steps merged into feature branch |
| SDK scope support | In progress | **Done** — `scoped_sdk` fixture works |
| Fix PR metadata loss | Not scheduled | Still not scheduled |
| Worktree branch mismatch | Not scheduled | Still not scheduled |

### Tier 3 (Test hardening) — Largely unchanged

| Item | Draft 36 status | Current status |
|------|----------------|----------------|
| Guard chain composition test | Not scheduled | Not scheduled |
| Spawn strategy selection tests | Not scheduled | Not scheduled |
| Flow tests framework | Not scheduled | **Partial** — `test_flow.py` exists with scoped SDK, tests happy path + rejection |
| Scheduler pipeline integration test | Not scheduled | Not scheduled |

### Tier 4 (Architecture) — Pool model done, rest unchanged

| Item | Draft 36 status | Current status |
|------|----------------|----------------|
| One spawn path | Not scheduled | Not scheduled |
| Messages table | Not scheduled | Not scheduled |
| Smaller scheduler | Not scheduled | Not scheduled |

## Current Test Inventory

**704 tests across 44 files.** Breakdown:

| Category | Tests | Coverage |
|----------|-------|----------|
| E2E lifecycle (real server) | ~30 | Task CRUD, state machine, claims, rejections, flow transitions |
| Git operations | ~30 | Worktrees, rebasing, conflict detection, push failures, network errors |
| Scheduler | ~40 | Guards, agent evaluation, housekeeping |
| Hooks/steps | ~19 | Hook resolution, execution, task types |
| Queue utils | ~30 | Queue operations, state management |
| Config | ~15 | Agent config parsing (pool model) |
| Pool tracking | ~6 | PID tracking, cleanup, capacity |
| Other (repo mgr, etc.) | rest | Various |

## The Big Gaps

### 1. No Claude/LLM agent mocking — CRITICAL GAP

This was identified in draft 36 and **nothing has changed**. There's no way to test the full agent lifecycle without calling Claude. Draft 40 ("Mock Claude agents in tests") has a detailed design for this but it was never implemented.

What we need: a test harness that simulates an agent claiming a task, writing code, producing `result.json`, and returning. This would let us test:
- Guard chain → claim → spawn → result handling → flow dispatch
- What happens when agents fail, crash, return bad results
- Gatekeeper reject/approve flows without Claude

**This is still the single biggest testing gap.** Without it, the scheduler's critical path (the thing draft 36 called "most dangerous") remains untested end-to-end.

### 2. No pool model concurrency tests — NEW GAP

The pool model just landed but has zero tests for:
- Multiple instances running simultaneously
- Capacity limits being respected
- The duplicate-claim bug we just discovered (TASK-pool-dedup-claim)
- Dead PID cleanup under concurrent access

### 3. No git conflict *resolution* tests

Tests cover conflict **detection** well (rebase conflict returns failure, network errors handled). But nothing tests the full resolution flow:
- Agent gets rejected with rebase instructions → re-claims → rebases → retries
- What happens when rebase itself has conflicts

### 4. Scheduler pipeline still untested

Draft 36's "most dangerous gap" remains: no test covers incoming → scheduler tick → agent evaluated → guard chain → claim → spawn. The unit tests mock individual guards but don't test the full pipeline composition.

## What's Working Well

1. **E2E test infrastructure is solid.** `scoped_sdk` gives real isolation, test server on port 9787 works, fixtures are clean.
2. **Git failure testing is comprehensive.** Error paths are well-covered at the unit level.
3. **Flow transitions have real tests.** `test_flow.py` tests happy path and rejection against real server.
4. **Pool model unit tests exist.** Basic PID tracking is covered.

## Recommended Next Actions

1. **Implement mock Claude agent harness** (draft 40). This unblocks testing the entire scheduler → agent → result → flow pipeline without calling Claude. It's the single highest-leverage testing improvement.
2. **Add pool concurrency tests.** Especially duplicate-claim prevention (once TASK-pool-dedup-claim lands).
3. **Add scheduler pipeline integration test.** Use the mock agent harness to test guard chain → claim → spawn → result → flow dispatch as one connected test.

## Summary

We're significantly better than 24 hours ago — Tier 1 blockers are resolved, pool model is live, gatekeeper works, error logging exists. But the **testing pyramid is bottom-heavy**: lots of unit tests with mocked dependencies, good E2E for API/lifecycle, but the critical middle layer (scheduler pipeline with mock agents) is completely missing. Draft 40's mock agent design is the key to closing this gap.
