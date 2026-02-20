# Test Suite Health Analysis — Post-Project Review

**Status:** Active
**Captured:** 2026-02-20
**Project:** PROJ-f58c4adc (Testing Infrastructure)

## Summary

Post-completion review of the testing project. 715 tests across 52 files. 637 passed, 48 failed, 30 skipped. All 18 project tasks were delivered. The failures and skips do not indicate system breakage — they indicate a cleanup pass is needed, plus one real production bug was found.

## Test Results Breakdown

### Failures (48)

| Category | Count | Verdict | Fix |
|---|---|---|---|
| Missing `branch` field on `sdk.tasks.create()` | 46 | Test gap (server change on 2026-02-17 made `branch` required) | Add `branch="main"` to 5 test files |
| `test_init.py` stale assertion | 1 | Test staleness (init output changed from `--skills` to `install-commands`) | Update assertion |
| `test_scheduler_poll.py` POST vs PUT | 1 | **REAL BUG** — `load_config` import doesn't exist in `config.py` | Fix `scheduler.py:1997` |

### The Production Bug

`scheduler.py:1997` imports `from .config import load_config` — but `load_config` does not exist. The `ImportError` is silently swallowed by a broad `except Exception` handler. This means **orchestrator registration has been silently broken** — the POST to `/api/v1/orchestrators/register` never fires. The correct import is `_load_project_config`.

### Skips (30)

| Category | Count | Risk | Action |
|---|---|---|---|
| Dead `diagnose_queue_health` script tests | 16 | High — dead code from v1 file-based system | Delete `test_queue_diagnostics.py` and `test_queue_auto_fixes.py` |
| `create_task.py` subprocess env issue | 12 | Medium — validation logic never tested | Fix `find_parent_project()` to respect `ORCHESTRATOR_DIR` |
| Custom queue flows (server feature not deployed) | 2 | Low — expected, self-resolving | Leave as-is |

## Coverage Assessment

The testing project shifted Octopoid from almost no integration testing to 133 integration tests running against a real local server. Key paths proven to work:

- Full task lifecycle (incoming -> claimed -> provisional -> done)
- Failure recovery (push failures, merge failures, expired leases, dead PIDs)
- Rejection cycles (multi-round reject/re-claim with conflict detection)
- Concurrency (pool capacity, duplicate claim prevention, priority ordering)
- Flow engine (script conditions, multi-condition short-circuit)
- Health checks (expired lease detection and bulk requeue)

### Remaining Gaps

1. **Scheduler main loop** — no test runs `run_scheduler()` tick cycle end-to-end (Draft #27)
2. **CI pipeline** — no GitHub Actions workflow runs these tests automatically
3. **Dashboard interaction** — only import/format tests, no Textual TUI testing
4. **Agent continuation** — queue transition tested but re-claim cycle is not
5. **Multi-orchestrator** — all tests use single orchestrator ID

## Action Items

### Quick fixes (can be done now)

1. Fix `scheduler.py:1997`: change `from .config import load_config` to `from .config import _load_project_config as load_config`
2. Add `branch="main"` to `sdk.tasks.create()` in 5 test files (46 failures)
3. Update `test_init.py:312` assertion from `"--skills"` to `"install-commands"`
4. Delete `tests/test_queue_diagnostics.py` and `tests/test_queue_auto_fixes.py` (16 dead skips)

### Larger fixes (enqueue as tasks)

5. Fix `find_parent_project()` to respect `ORCHESTRATOR_DIR` env var (un-skip 12 tests)
6. Set up CI workflow to run integration tests (Draft #27 scope)
7. Write Level 3 scheduler loop test (Draft #27)

## Verdict

**PROJ-f58c4adc was successful.** The test suite provides genuine confidence that core lifecycle, flow engine, recovery, and concurrency work correctly. The 48 failures are from test gaps, not system bugs — except one real production bug in orchestrator registration that the tests correctly identified.
