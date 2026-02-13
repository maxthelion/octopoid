# [TASK-ec0c0d5f] Fix integration tests hitting production server

ROLE: implement
PRIORITY: P1
BRANCH: feature/client-server-architecture
CREATED: 2026-02-13T00:00:00Z
CREATED_BY: human
EXPEDITE: false
SKIP_PR: true

## Context

Integration tests are hitting the production Cloudflare server instead of the local test server (port 9787). This causes:
- Junk test data ("First task", "Part B", "Empty string blocker", etc.) polluting the production task queue
- Flaky test results depending on production state
- 6 test failures due to unexpected data or state on prod

See: `project-management/drafts/2-2026-02-13-fix-test-failures.md` (Category 2)

Failing tests:
- `tests/integration/test_claim_content.py::TestClaimReturnsContent::test_claim_task_includes_file_content`
- `tests/integration/test_hooks.py::TestTaskTypeAPI::test_type_survives_lifecycle`
- `tests/integration/test_hooks.py::TestServerHooks::test_hooks_persist_through_lifecycle`
- `tests/integration/test_task_lifecycle.py::TestBasicLifecycle::test_create_claim_submit_accept`
- `tests/integration/test_task_lifecycle.py::TestBasicLifecycle::test_claim_submit_reject_retry`
- `tests/integration/test_task_lifecycle.py::TestBasicLifecycle::test_multiple_rejections`
- `tests/integration/test_task_lifecycle.py::TestClaimBehavior::test_claim_returns_none_when_no_tasks`

## Changes Required

### 1. Fix integration test conftest to enforce local server
- `tests/integration/conftest.py` must set `OCTOPOID_SERVER_URL=http://localhost:9787` for all fixtures
- The SDK fixture should never point at production
- Add a safeguard: fail fast if the URL contains `workers.dev` during tests

### 2. Add proper cleanup fixtures
- Tests should clean up tasks they create (delete on teardown)
- Or use unique prefixes and batch-delete in teardown
- The existing `clean_tasks` fixture may need updating

### 3. Prevent unit tests from hitting production
- Audit `tests/conftest.py` for any SDK fixtures that don't mock the network layer
- Any test that instantiates a real SDK should be in `tests/integration/` and use the local server

### 4. Verify test server startup
- `tests/integration/bin/start-test-server.sh` starts wrangler on port 9787
- Confirm this still works with current wrangler config (`packages/server/wrangler.test.toml`)
- Integration tests should skip gracefully if the test server isn't running

## Acceptance Criteria

- [ ] All integration tests use `http://localhost:9787`, never production
- [ ] Integration test conftest has a guard against `workers.dev` URLs
- [ ] Tests clean up their own data on teardown
- [ ] No junk tasks appear on production after running `pytest`
- [ ] The 6 integration test failures pass against the local test server
- [ ] Unit tests don't make real network requests
