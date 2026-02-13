# Fix Remaining Gaps from Agent Architecture Completion Plan

## Context

The agent architecture work (see `project-management/drafts/agent-architecture-completion-plan.md`) is mostly complete, but verification found 5 gaps. This plan covers fixing them. All changes are low-risk — no behavioral changes, just cleanup and docs.

---

## 1. D3: Actually consolidate hooks.py into hook_manager.py

**Problem:** `KNOWN_HOOKS` and `DEFAULT_HOOKS` were duplicated into `hook_manager.py`, but `orchestrator/hooks.py` still exists with its own copies plus the full execution pipeline. Multiple files still import from hooks.py.

**What to do:**

The execution pipeline in hooks.py (`HookContext`, `HookPoint`, `HookStatus`, `run_hooks`, `resolve_hooks`, individual hook functions) is still actively used by:
- `orchestrator/queue_utils.py` (line ~2700): `from .hooks import HookContext, HookPoint, HookStatus, run_hooks`
- `orchestrator/roles/implementer.py` (line 26): same imports
- `tests/test_hooks.py`: imports `BUILTIN_HOOKS`, `DEFAULT_HOOKS`, hook functions
- `tests/integration/test_hooks.py`: imports `BUILTIN_HOOKS`, hook functions

Since implementer.py deletion is deferred (Phase D2), and the execution pipeline is coupled to it, the right fix now is:

1. Remove the duplicate `BUILTIN_HOOKS`/`DEFAULT_HOOKS` from `hooks.py` — keep only the copies in `hook_manager.py`
2. Update any imports of these constants to point to `hook_manager` instead of `hooks`
3. Leave the execution pipeline (`run_hooks`, `resolve_hooks`, hook functions, `HookContext`, `HookPoint`, `HookStatus`) in `hooks.py` for now — it gets deleted with implementer.py in Phase D2
4. Add a comment at the top of `hooks.py`: `# Legacy execution pipeline — will be removed with implementer.py (Phase D2)`

**Files to modify:**
- `orchestrator/hooks.py` — remove `BUILTIN_HOOKS` and `DEFAULT_HOOKS` dicts, add legacy comment
- `tests/test_hooks.py` — update imports of `BUILTIN_HOOKS`/`DEFAULT_HOOKS` to come from `hook_manager`
- `tests/integration/test_hooks.py` — same import update

**Verify:** Run `pytest tests/test_hooks.py tests/test_hook_manager.py tests/integration/test_hooks.py` — all should pass.

---

## 2. D4: Slim down base.py

**Problem:** `orchestrator/roles/base.py` (441 lines) still has implementer-specific methods that only the implementer role uses.

**What to do:**

This is intentionally deferred — the implementer-specific code in base.py (`invoke_claude()`, `_invoke_with_streaming()`, tool counter methods) should be removed when implementer.py is deleted (Phase D2). Removing it now would break implementer.py.

**Action: No changes needed.** The original summary incorrectly listed this as done ("removed dead `get_queue_dir()`"). Verify whether `get_queue_dir()` was actually removed — if yes, D4 is partially done and the rest waits for D2. If not, it was never touched.

**Verify:** `grep -n "get_queue_dir" orchestrator/roles/base.py` — if no results, partial credit. The rest is blocked on D2.

---

## 3. E2: Update v2 implementation status

**Problem:** `docs/v2-IMPLEMENTATION_STATUS.md` still shows old test counts.

**What to do:**

1. Open `docs/v2-IMPLEMENTATION_STATUS.md`
2. Find the test count line (currently says "22 unit + 22 integration hook tests")
3. Get actual counts by running:
   - `pytest tests/test_hook_manager.py --co -q | tail -1` (unit tests for hook_manager)
   - `pytest tests/test_hooks.py --co -q | tail -1` (unit tests for hooks)
   - `pytest tests/test_repo_manager.py --co -q | tail -1` (unit tests for repo_manager)
   - `pytest tests/integration/ --co -q | tail -1` (integration tests)
4. Update the line with accurate counts
5. Update the percentage if appropriate

**Files to modify:**
- `docs/v2-IMPLEMENTATION_STATUS.md`

---

## 4. E3: Fix migration guide

**Problem:** `docs/migration-v2.md` references an `admin/import` API endpoint that doesn't exist.

**What to do:**

1. Open `docs/migration-v2.md`
2. Find the section about importing tasks via API (around lines 81-91)
3. Replace it with a note that v1→v2 task migration is manual:
   ```markdown
   ### Step 3: Import Tasks (Manual)

   There is no automated import endpoint. To migrate tasks from v1:
   1. Export from `.orchestrator/state.db` using SQLite tools: `sqlite3 .orchestrator/state.db ".dump tasks"`
   2. Create tasks in v2 using the API: `POST /api/tasks` with each task's data
   ```
4. Remove any reference to nonexistent endpoints

**Files to modify:**
- `docs/migration-v2.md`

---

## 5. E5: Delete planning docs

**Problem:** `AGENT_ARCHITECTURE_REFACTOR.md` and `initialhooks.md` still in project root.

**What to do:**

These should stay until Phase D2 (implementer.py deletion) is complete, since they're still useful references. **No action now.**

When Phase D2 is done, delete:
- `/Users/maxwilliams/dev/octopoid/AGENT_ARCHITECTURE_REFACTOR.md`
- `/Users/maxwilliams/dev/octopoid/initialhooks.md`

---

## Execution Order

```
1. D3 — consolidate hooks constants (removes duplication, no behavior change)
2. E2 — update test counts (docs only)
3. E3 — fix migration guide (docs only)
4. D4 — verify get_queue_dir removal, note rest is blocked on D2
5. E5 — no action (blocked on D2)
```

Items 1-3 are independent and can be done in parallel. Total effort: ~30 minutes.
