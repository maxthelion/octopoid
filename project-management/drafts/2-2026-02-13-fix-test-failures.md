# Fix Pre-existing Test Failures

**Status:** Idea
**Captured:** 2026-02-13

## Raw

> outline a plan for the 13 pre-existing test failures

## Idea

After the database purge (TASK-719652be), there are 20 test failures remaining (originally reported as 13, but the count grew slightly during the purge work). These are all pre-existing — none were caused by the purge itself. They fall into three clear categories that can be fixed independently.

## Failure Breakdown

### Category 1: Compaction hook tests (11 failures)
**Files:** `tests/test_compaction_hook.py`

All 11 failures are `FileNotFoundError` for `/Users/maxwilliams/dev/.claude/hooks/write-compaction-checkpoint.sh` — the tests expect a hook script at a hardcoded path that doesn't exist. This is either:
- A path that changed when the project restructured (`.claude/hooks/` → somewhere else)
- A feature that was designed but never fully implemented
- Tests that assume a parent project layout that doesn't match reality

**Fix:** Either create the missing hook script, update the paths, or delete these tests if the feature is obsolete.

### Category 2: Integration test failures (6 failures)
**Files:** `tests/integration/test_claim_content.py`, `tests/integration/test_hooks.py`, `tests/integration/test_task_lifecycle.py`

These are hitting the production server and failing. Key issues:
- `test_claim_returns_none_when_no_tasks` — probably fails because there are tasks in the queue
- Lifecycle tests — likely data pollution from other test runs or schema mismatches after the drafts migration
- These tests were also **creating junk data on production** (the "First task", "Part B" etc. we had to clean up)

**Fix:** These need to run against the local test server (port 9787) exclusively. The `conftest.py` fixtures should enforce `OCTOPOID_SERVER_URL=http://localhost:9787`. Also need proper cleanup fixtures.

### Category 3: Tool counter hook test (1 failure)
**File:** `tests/test_tool_counter.py::TestHookScript::test_hook_appends_byte_with_agent_name`

`AssertionError` — expects a file at `.octopoid/runtime/agents/test-agent/tool_counter` that doesn't get created. Similar to the compaction hook — the hook script that writes this file is missing or the path changed.

**Fix:** Verify the tool counter hook exists and writes to the expected path, or update the test.

### Category 4: Test sandboxing (cross-cutting)
Integration tests are polluting the production server. This needs a systemic fix:
- All integration tests must target the local test server
- Unit tests that create SDK instances must mock the network layer
- Consider a pytest fixture that fails fast if `OCTOPOID_SERVER_URL` points at production during tests

## Open Questions

- Is the compaction hook feature still wanted? If not, delete all 11 tests
- Should we keep the tool counter hook? Same question
- What's the right cleanup strategy for integration tests — delete-on-teardown, or use unique prefixes and batch-delete?

## Possible Next Steps

- **Quick win:** Fix integration test `conftest.py` to use local server only (fixes 6 + prevents data pollution)
- **Decide:** Keep or kill compaction hook tests (fixes 11)
- **Small fix:** Tool counter path update (fixes 1)
- Create a task per category, or one task for all 20
