# Integration Tests - Octopoid v2.0 API-only Architecture

End-to-end integration tests for the API server, SDK, scheduler, and agents.

## Quick Start

```bash
# Install dependencies
pip install pytest octopoid-sdk

# Run all tests (starts/stops server automatically)
./tests/integration/run_tests.sh

# Run specific test file
./tests/integration/run_tests.sh tests/integration/test_api_server.py

# Run specific test
./tests/integration/run_tests.sh tests/integration/test_api_server.py::TestServerHealth::test_health_endpoint
```

## Manual Test Server Control

```bash
# Start test server (port 9787)
./tests/integration/bin/start-test-server.sh

# Run tests without auto-start/stop
pytest tests/integration/ -v

# Stop test server
./tests/integration/bin/stop-test-server.sh
```

## Test Structure

```
tests/integration/
├── bin/
│   ├── start-test-server.sh      # Start test server on port 9787
│   └── stop-test-server.sh       # Stop test server
├── fixtures/                     # Test data fixtures
├── conftest.py                   # Pytest fixtures and config
├── test_api_server.py            # API endpoint tests
├── test_hooks.py                 # Hooks system and task type tests
├── test_task_lifecycle.py        # Task lifecycle and state machine tests
├── test_scheduler_mock.py        # Scheduler lifecycle tests using mock agents
├── test_git_failure_scenarios.py # Git failure scenario tests using mock agents
└── run_tests.sh                  # Main test runner
```

## Test Suites

**Total: 54 tests, all passing ✅**

### 1. API Server Tests (`test_api_server.py`) - 17 tests

**Server Health (2 tests)**
- Health endpoint responds with status
- Health check includes timestamp

**Orchestrator API (5 tests)**
- Register new orchestrator
- Re-register orchestrator (idempotent updates)
- List all orchestrators
- Get orchestrator by ID
- Send heartbeat

**Task CRUD (10 tests)**
- Create, read, update, delete tasks
- List and filter tasks by queue
- Update task fields
- Validation and error handling
- Duplicate task detection
- Task creation with metadata

### 2. Hooks System Tests (`test_hooks.py`) - 14 tests

**Task Type API (5 tests)**
- Create task with type field
- Create task without type (null)
- Type persists on read
- Update task type
- Type survives full lifecycle (claim → submit → accept)

**Hook Resolution with Config (4 tests)**
- Resolve hooks from project config.yaml
- Type-specific hooks override project-level
- Unknown type falls through to project hooks
- No config uses defaults (just create_pr)

**Hook Execution (5 tests)**
- Rebase skips when up to date
- run_tests detects pytest from real directory
- Full pipeline resolved from config
- Pipeline fail-fast (failed hook stops execution)
- Pipeline uses type-specific hooks

### 3. Mock Agent Tests (`test_scheduler_mock.py`) - 7 tests

**Happy Path (1 test)**
- Full cycle: create → claim → mock implementer → provisional → mock gatekeeper approve → done

**Failure Scenarios (2 tests)**
- Agent failure result moves task to failed queue
- Agent crash (no result.json) moves task to failed queue

**Gatekeeper Flows (2 tests)**
- Gatekeeper reject returns task to incoming with feedback
- Multiple rejections increment rejection count

**Edge Cases (2 tests)**
- Minimal commits (1 commit) reaches provisional successfully
- needs_continuation outcome moves task to needs_continuation queue

### 4. Git Failure Scenario Tests (`test_git_failure_scenarios.py`) - 6 tests

**Merge Conflict Scenarios (2 tests)**
- CONFLICTING PR blocks gatekeeper acceptance; guard rejects task back to incoming
- gh pr merge failure moves task to failed (not done)

**Push Failure Scenarios (2 tests)**
- Push branch failure leaves task in claimed (not orphaned)
- Push with no diff (branch already up-to-date) still reaches provisional

**Rebase Instructions (1 test)**
- Gatekeeper rejection appends rebase instructions referencing the correct base branch

**Combined Scenarios (1 test)**
- Reject → re-claim → still CONFLICTING → guard rejects again without getting stuck

### 5. Task Lifecycle Tests (`test_task_lifecycle.py`) - 10 tests

**Basic Lifecycle (3 tests)**
- Full lifecycle: create → claim → submit → accept → done
- Rejection flow: claim → submit → reject → incoming
- Multiple rejections

**Claim Behavior (4 tests)**
- Claim with role filter
- Claim returns none when no tasks available
- Claim respects priority ordering
- Claim updates claimed_by and claimed_at

**State Validation (3 tests)**
- Cannot submit unclaimed task
- Cannot accept unclaimed task
- Cannot claim from wrong queue

## Server as Submodule

The server lives in its own repository ([octopoid-server](https://github.com/maxthelion/octopoid-server))
and is included here as a git submodule at `submodules/server/`. It has no dependency
on `@octopoid/shared` or the monorepo root `tsconfig.base.json` — shared types
are self-contained in `submodules/server/src/types/shared.ts`.

To initialize the submodule after cloning:

```bash
git submodule update --init
```

Integration tests verify that the server's copy of shared types hasn't drifted
from the canonical `packages/shared/` definitions.

## Test Server

The integration tests use a separate test server instance:
- **Port**: 9787 (different from dev server on 8787)
- **Database**: octopoid-test (separate D1 database)
- **Configuration**: `submodules/server/wrangler.test.toml`

This ensures tests don't interfere with development work.

## Writing Tests

### Test Fixtures

Available fixtures in `conftest.py`:

```python
def test_example(sdk, orchestrator_id, clean_tasks):
    # sdk: OctopoidSDK instance connected to test server
    # orchestrator_id: Test orchestrator identifier
    # clean_tasks: Cleans test tasks before/after test

    task = sdk.tasks.create(
        id="test-example",
        file_path="/tmp/test-example.md",
        title="Example Test",
        role="implement"
    )
    assert task['id'] == "test-example"
```

### Test Naming Convention

- Prefix test task IDs with: `test-`, `lifecycle-`, `mock-`, etc.
- Tests use `clean_tasks` fixture to clean up automatically
- Test classes group related functionality

### Markers

Use pytest markers to categorize tests:

```python
@pytest.mark.slow
def test_long_running_operation(sdk):
    # Slow test
    pass

@pytest.mark.api
def test_api_endpoint(sdk):
    # API-specific test
    pass
```

Run specific markers:
```bash
# Run only API tests
pytest -m api

# Skip slow tests
pytest -m "not slow"
```

## Current Status

**✅ Implemented:**
- Test server infrastructure
- Setup/teardown scripts
- API server tests
- Task lifecycle tests
- Basic fixtures

**🚧 TODO:**
- Concurrency tests
- Queue utils integration tests
- Performance benchmarks

## Troubleshooting

### Server won't start
```bash
# Check if server is already running
lsof -i :9787

# Kill existing server
pkill -f "wrangler.*9787"

# Check server logs
cat /tmp/octopoid-test-server.log
```

### Tests fail with connection error
- Ensure test server is running: `curl http://localhost:9787/api/health`
- Check firewall/network settings
- Verify wrangler is installed: `npx wrangler --version`

### Database issues
- Test database is ephemeral (in-memory D1)
- Recreated on each server start
- No manual migration needed

## CI/CD

Tests can run in GitHub Actions:

```yaml
# .github/workflows/integration-tests.yml
- name: Run integration tests
  run: |
    npm install -g wrangler
    pip install pytest octopoid-sdk
    ./tests/integration/run_tests.sh
```

See `TESTING_PLAN.md` for full CI/CD configuration.
