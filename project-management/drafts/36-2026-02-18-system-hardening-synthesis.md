# System Hardening Synthesis: Where We Are, What Remains

**Status:** Active
**Date:** 2026-02-18
**Sources:** Drafts 25-35 (2026-02-17), today's debugging session

## Goals

Three interconnected goals emerged from 2026-02-17's analysis:

1. **Outside-in testing** — stop mocking the server, test real code paths, catch regressions before they accumulate
2. **Pure-function agent model** — agents return results, orchestrator drives lifecycle. Fewer code paths, fewer silent failures
3. **System hardening** — delete dead code, fix scattered state, make failures loud

## What's Been Accomplished

### Pure-function model: partially landed

- **Implementer** converted to pure function (TASK-2bf1ad9b, merged PR #74). Agent writes code and returns `result.json`. Orchestrator handles push, PR, submit via `handle_agent_result_via_flow()`.
- **Flow module wired into scheduler** (draft #35). `default.yaml` defines transitions with `runs:` steps. Step registry (`orchestrator/steps.py`) holds named functions. Result handling now flow-driven, not hardcoded per-role.
- **Flow dispatch guard added** (TASK-46eb663d, in provisional). Protects against unknown decision values in `result.json`.

### Testing infrastructure: foundation exists

- **Server scope support** landed (migration `0009_add_scope.sql`). All CRUD routes filter by `?scope=`.
- **Integration test suite** running (27 tests against real server on port 9787). Covers task CRUD, lifecycle transitions, orchestrator API.
- **SDK scope support** in progress (TASK-c8953729 / draft #32). Once done, each test gets isolated data.

### Debugging improvements

- **Orchestrator registration fixed** — `repo_url` now set correctly, unblocking all task claims (was causing cascade: registration 400 → claim 500 → all agents stuck).
- **Error logging task created** (TASK-27adf598) — scheduler will log server response bodies instead of just status codes.

## What Remains

### Tier 1: Blocking — agents can't function without these

| Work | Status | Draft/Task | Notes |
|------|--------|------------|-------|
| Fix gatekeeper: delete Python role, fix claim role_filter | Claimed (TASK-b0a63d8b) | Draft #29 | P0. Currently two code paths, neither works. Pure-function gatekeeper is designed but not wired in. |
| Fix `get_agents()` dropping gatekeeper | Not scheduled | — | `config.py` skips entries without `type:` field. Gatekeeper has `role:` but no `type:`. Silent failure. |
| Log server error response bodies | Incoming (TASK-27adf598) | — | Three places in scheduler swallow HTTP error details. |

### Tier 2: Reliability — prevents recurring failures

| Work | Status | Draft/Task | Notes |
|------|--------|------------|-------|
| Fix PR metadata loss (atomic submit) | Not scheduled | Draft #25 | Tasks in provisional with no `pr_number`. Submit endpoint should accept PR info in same request. Server + client change. |
| Worktree branch mismatch detection | Not scheduled | Draft #26 | Scheduler reuses stale worktrees on wrong branch. Need branch check before spawn. |
| SDK scope support | In progress | Draft #32, TASK-c8953729 | Unblocks scoped testing. Small SDK change. |
| Pool model (4-task project) | Incoming, sequential | TASK-134eb961 → 5e5eebd1 → 6b1d5556 → 7ac764e6 | Blueprint-based config, PID tracking, pool guard. Replaces fragile fleet list. |

### Tier 3: Test hardening — catch regressions automatically

| Work | Status | Draft/Task | Notes |
|------|--------|------------|-------|
| Guard chain composition test | Not scheduled | Draft #27 | Assert `AGENT_GUARDS` contains expected guards in order. Would have caught d858559 deletion. |
| Spawn strategy selection tests | Not scheduled | Draft #27 | Unit tests: given config, assert correct spawn path. |
| Flow tests framework | Not scheduled | Draft #33, phase 2 | Test orchestrator lifecycle against real scoped server. "If gatekeeper returns reject, does task go back to incoming?" No Claude needed. |
| Scheduler pipeline integration test | Not scheduled | Draft #27 | Create task → scheduler tick → task claimed → agent started. Critical path, completely untested today. |

### Tier 4: Architecture — cleaner long-term model

| Work | Status | Draft/Task | Notes |
|------|--------|------------|-------|
| Messages table (actor mailboxes) | Not scheduled | Draft #34 | Replaces `result.json` on disk, enables rejection context across sessions, mid-flight feedback. Future work — depends on pure-function model being proven. |
| One spawn path | Not scheduled | Draft #30 | Delete `spawn_lightweight`, `spawn_worktree`. Every agent spawns the same way. Follows from pure-function model. |
| Smaller scheduler | Not scheduled | Draft #30 | Extract task directory prep, prompt rendering, result handling into modules. Scheduler becomes thin loop. |

## Old Code Paths to Delete

These are dead or competing code paths identified in drafts #29 and #30 that need cleanup:

1. **`orchestrator/roles/sanity_check_gatekeeper.py`** — 938 lines, 0 successes ever. Covered by TASK-b0a63d8b.
2. **`spawn_lightweight()` in scheduler** — used for agents with `lightweight: true`. Should be folded into single spawn path once pure-function model is proven.
3. **`spawn_worktree()` fallback** — catches cases where `spawn_mode` isn't `scripts`. Should not exist once all agents use the same model.
4. **`create_feature_branch()` in git_utils** — older function, not used in detached-HEAD flow. Can delete.
5. **Multi-site `get_sdk()` mocking** in test conftest — 4 nested `patch()` calls. Replace with scoped SDK fixture once TASK-c8953729 lands.
6. **`gatekeeper_reviewed` / `gatekeeper_approved` fields** referenced in old role module — don't exist in schema. Dead references.

## Recommended Sequencing

Based on draft #33's phasing, adjusted for current state:

### Now (in flight)
- TASK-b0a63d8b: Fix gatekeeper (P0, claimed)
- TASK-27adf598: Error logging (P1, incoming)
- TASK-c8953729: SDK scope (in progress elsewhere)

### Next: test hardening sprint
1. **Guard chain + spawn strategy tests** (draft #27, levels 1-2). Pure unit tests, no infrastructure needed. Prevents future d858559-style deletions.
2. **Flow tests framework** (draft #33, phase 2). Use scoped SDK to test lifecycle transitions. Start with happy path + reject path for gatekeeper flow.
3. **Scheduler pipeline test** (draft #27, level 3). One test that exercises incoming → claimed → spawn. Requires test server.

### Then: reliability fixes
4. **Fix PR metadata loss** (draft #25). Atomic submit. Server + client.
5. **Worktree branch mismatch** (draft #26). Branch check before spawn.
6. **Fix `get_agents()` dropping entries without `type:`**. One-line fix in config.py.

### Then: pool model
7. Pool model project (4 sequential tasks, already scheduled)

### Later: architecture
8. One spawn path (after pool model proves blueprint config)
9. Messages table (after pure-function model is proven with both gatekeeper and implementer)

## Key Insight

Draft #30 nailed the root cause: **v1 plumbing carrying v2 data**. The API-only migration removed `is_db_enabled()` checks but didn't rethink spawn/state/lifecycle. The pure-function model (draft #31) and flows (draft #35) are the right answers — they're partially landed. The test infrastructure (drafts #28, #32, #33) ensures we can make further changes without breaking what works.

The most dangerous gap right now is **no tests on the scheduler's critical path**. Every breakage this week was in the guard chain → claim → spawn pipeline, and zero tests cover it.

## Open Questions (from drafts, still unresolved)

1. Should agents run tests themselves for iteration, with orchestrator re-running as verification? Or strictly orchestrator-only? (Draft #31)
2. Should `claim_for_review` keep tasks in `provisional` or move to a `reviewing` state? (Draft #29)
3. Should flow tests live in `tests/integration/` or new `tests/flows/`? (Draft #33)
4. Should messages be added alongside or replace `task_history` events table? (Draft #34)
