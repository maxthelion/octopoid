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
# Start test server (port 8788)
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
│   ├── start-test-server.sh   # Start test server on port 8788
│   └── stop-test-server.sh    # Stop test server
├── fixtures/                  # Test data fixtures
├── conftest.py               # Pytest fixtures and config
├── test_api_server.py        # API endpoint tests
├── test_task_lifecycle.py    # Task lifecycle and state machine tests
└── run_tests.sh              # Main test runner
```

## Test Suites

### 1. API Server Tests (`test_api_server.py`)
- Health endpoint validation
- Task CRUD operations
- List and filter tasks
- Update and delete tasks
- Validation and edge cases

### 2. Task Lifecycle Tests (`test_task_lifecycle.py`)
- Complete lifecycle: create → claim → submit → accept
- Rejection flow: submit → reject → retry
- Claim behavior and role filtering
- State machine validation
- Priority ordering

## Test Server

The integration tests use a separate test server instance:
- **Port**: 8788 (different from dev server on 8787)
- **Database**: octopoid-test (separate D1 database)
- **Configuration**: `packages/server/wrangler.test.toml`

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
- Mock agent tests
- Queue utils integration tests
- Performance benchmarks

## Troubleshooting

### Server won't start
```bash
# Check if server is already running
lsof -i :8788

# Kill existing server
pkill -f "wrangler.*8788"

# Check server logs
cat /tmp/octopoid-test-server.log
```

### Tests fail with connection error
- Ensure test server is running: `curl http://localhost:8788/api/health`
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
