# Postmortem: CI red on main for 4 days (78 consecutive failures)

**Date:** 2026-03-02
**Duration:** Feb 27 10:36 UTC → ongoing (4+ days)
**Impact:** All CI runs on main failing. 48+ PRs merged to red main without detection.

## Timeline

- **Feb 27 10:36** — Last green CI on main (PR #251, "Replace print/debug_log with unified Python logging")
- **Feb 27 12:13** — PR #252 merged ("Add requires-intervention queue and fixer agent"). CI breaks: 6 tests fail (5 NameError: `debug_log` not defined, 1 assertion mismatch)
- **Feb 27 ~15:00** — PR #261 merged ("Route all agent failures through intervention"). Adds 6 more test failures: tests expect `queue="failed"` but `fail_task()` now sets `needs_intervention=True` without changing queue.
- **Feb 28 ~17:43** — PR #264 merged ("Add async checks to flow system"). Adds `check_ci` for PR branches. Does not catch main-branch CI failures.
- **Feb 28 onwards** — More PRs merge, accumulating to 13 integration test failures. Each PR's branch CI may pass (tests not affected on the branch) but main stays red.
- **Mar 2** — Issue diagnosed during task `5007934d` investigation.

## Root Cause

**Primary:** PR #252 introduced `fail_task()` and `request_intervention()` but the new code used `debug_log()` (removed in a previous PR) instead of `logger`. 5 unit tests failed immediately with NameError.

**Secondary:** PR #261 changed the failure routing model — `_handle_fail_outcome()` now calls `fail_task()` → `request_intervention()` which sets `needs_intervention=True` but does NOT change the task queue. Tests expecting `queue="failed"` or `queue="requires-intervention"` see the task still in `"claimed"` or `"provisional"`. This broke 6 more integration tests.

**Tertiary:** Additional test failures accumulated from later PRs (FK constraint changes, flow step pipeline changes, mock fixture keyword precedence).

## Why It Wasn't Caught

### 1. check_ci checks the PR branch, not main

The `check_ci` async check (task 1ea7b68d, PR #264) polls `gh pr checks <N>` — the CI status of the **PR branch**. If a PR's branch is green (it doesn't touch the broken tests), `check_ci` passes and the gatekeeper can approve and merge.

The gap: there is no check that **main is green** before merging. A PR can pass its own CI while merging into a broken main.

### 2. No post-rebase CI verification

The flow's terminal steps are: `rebase_on_base` → `merge_pr`. After `rebase_on_base` force-pushes the rebased branch, GitHub CI re-triggers automatically. But `merge_pr` runs immediately without waiting for the re-triggered CI to complete.

### 3. check_ci runs before the gatekeeper, not before merge

In the flow config, `check_ci` is a pre-condition check (runs before gatekeeper claims). The terminal runs (`rebase_on_base`, `merge_pr`) execute after gatekeeper approval. There is no second CI check between gatekeeper approval and merge.

### 4. No alerting on main CI status

There is no mechanism to notify when main goes red. The scheduler's `check_ci` only applies to individual task PRs, not to the main branch itself.

## Impact

- 78 consecutive red CI runs over 4 days
- 48+ PRs merged without CI protection on main
- Agent tasks that depend on CI passing (like task `5007934d` — "Fix CI") entered loops where the fix was incomplete because the scope kept growing
- Reduced confidence in the test suite — if tests have been red for days, developers may start ignoring failures

## Fixes

### Immediate (enqueued)

Task `570d1d48` (P0): Fix all 13 failing integration tests. Four root causes:
1. `request_intervention()` must set `queue="requires-intervention"` (not just a flag)
2. Mock inference fixture keyword precedence in conftest.py
3. Orchestrator FK + scope in test_sdk_client.py
4. Git scenario tests need to account for auto-injected terminal steps

### Structural (needed)

1. **Add main-branch CI gate.** Before `merge_pr`, check that main's latest CI run is green. If main is red, block the merge and alert. This prevents cascading failures.

2. **Add post-rebase CI wait.** After `rebase_on_base`, wait for the re-triggered CI to complete before running `merge_pr`. This catches merge-induced regressions.

3. **Add CI alerting.** A scheduler job that checks main CI status periodically and pauses the system (or alerts) if main goes red.

## Lessons

- **PR-branch CI is necessary but not sufficient.** A green PR branch doesn't guarantee a green main after merge. The system needs main-branch CI awareness.
- **Test failures compound rapidly.** One broken PR introduced 6 failures. A second PR added 6 more. Within 2 days, the count reached 13. Broken CI is an emergency, not a backlog item.
- **Agents can't fix CI they don't understand.** Task `5007934d` tried to fix the tests but produced an incomplete fix because the root causes span multiple subsystems (code, fixtures, server schema, flow pipeline).

## Symptoms for Issues Log

- CI red on main for multiple days with no alert
- check_ci passes for PR branches while main CI is broken
- Tests asserting `queue="failed"` get `"claimed"` after intervention routing change
