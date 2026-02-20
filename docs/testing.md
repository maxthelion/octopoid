# Testing Guide

This document explains how to run, write, and extend tests for Octopoid.

## Testing Philosophy: Outside-In

Prefer end-to-end tests with a real local server over mocked unit tests. The hierarchy:

1. **Priority 1 — End-to-end:** Real local server + real SDK. Full lifecycle tests (create → claim → spawn → submit → accept). Use `scoped_sdk` for isolation.
2. **Priority 2 — Integration:** Real server, mocked agent spawn. API contract tests, flow transitions, migration correctness.
3. **Priority 3 — Unit:** Mocked dependencies. Only for pure logic (parsing, config merging, queue math) and error paths that are hard to trigger end-to-end.

**Rule of thumb:** Only mock `get_sdk()` when you need to test with specific return values (error paths, edge cases). Never mock just to avoid running the server.

---

## Test Directory Structure

```
tests/
├── conftest.py                     # Root fixtures — auto-mocks SDK for all unit tests
├── fixtures/
│   ├── conftest_mock.py            # Git repo + task directory fixtures
│   └── mock_helpers.py
├── integration/
│   ├── conftest.py                 # Integration fixtures (scoped_sdk, orchestrator_id)
│   ├── pytest.ini                  # Integration pytest config + markers
│   ├── bin/
│   │   ├── start-test-server.sh   # Wrangler dev on port 9787
│   │   └── stop-test-server.sh
│   ├── flow_helpers.py             # Reusable flow test utilities
│   ├── test_api_server.py
│   ├── test_task_lifecycle.py
│   ├── test_flow.py
│   └── ...                         # 20+ integration test files
├── test_scheduler_poll.py
├── test_queue_utils.py
└── ...                             # 40+ unit test files
```

---

## Running Tests

### Unit Tests

Unit tests run against mocked dependencies — no server required. The root `conftest.py` automatically patches `get_sdk()` for every test outside `tests/integration/`, so production is never touched.

```bash
# Run all unit tests
pytest tests/ --ignore=tests/integration

# Run a specific file
pytest tests/test_queue_utils.py

# Run a specific test
pytest tests/test_scheduler_poll.py::TestGuardBackpressure::test_no_api_calls

# Filter by name pattern
pytest tests/ -k "claim" --ignore=tests/integration

# Verbose output
pytest tests/ -v --ignore=tests/integration
```

### Integration Tests

Integration tests run against a real local server. Start it before running tests, then stop it when done.

```bash
# Start the test server (Wrangler dev on port 9787)
./tests/integration/bin/start-test-server.sh

# Run all integration tests
pytest tests/integration/ -v

# Run a specific test file
pytest tests/integration/test_task_lifecycle.py -v

# Skip slow tests
pytest tests/integration/ -v -m "not slow"

# Stop the server
./tests/integration/bin/stop-test-server.sh
```

The test server uses a separate D1 database (`octopoid-test`) configured in `submodules/server/wrangler.test.toml`. It runs on port 9787 to avoid conflicting with any local production instance.

---

## Key Fixtures

### Root Fixtures (`tests/conftest.py`)

These are available to all tests. Most are applied automatically.

| Fixture | Scope | Description |
|---|---|---|
| `mock_sdk_for_unit_tests` | function | **Auto-applied** outside `integration/`. Patches `get_sdk()` to return a `MagicMock`. Prevents any HTTP calls to production. |
| `temp_dir` | function | Temporary directory, cleaned up after the test. |
| `mock_orchestrator_dir` | function | Mocked `.octopoid/` structure with queue subdirectories and `agents.yaml`. |
| `mock_config` | function | Patches config functions to use `mock_orchestrator_dir`. |
| `sample_task_file` | function | Pre-created `TASK-abc12345.md` in the incoming queue. |
| `sample_task_with_dependencies` | function | Two task files with a `BLOCKED_BY` dependency. |

### Integration Fixtures (`tests/integration/conftest.py`)

| Fixture | Scope | Description |
|---|---|---|
| `scoped_sdk` | function | **Preferred.** SDK with a unique scope per test (`test-{uuid}`). Provides complete isolation without manual cleanup. |
| `orchestrator_id` | session | Registers a test orchestrator (`test-{hostname}`) against the test server. |
| `sdk` | session | Unscoped SDK connected to the test server. Avoid in favour of `scoped_sdk` unless you intentionally need cross-test visibility. |
| `clean_tasks` | function | Deletes all tasks before and after the test. Use only when `scoped_sdk` isolation is insufficient. |

### Git/Worktree Fixtures (`tests/fixtures/conftest_mock.py`)

| Fixture | Description |
|---|---|
| `test_repo` | Local git repo with a bare remote and working clone. No GitHub required. |
| `conflicting_repo` | Repo with a pre-built merge conflict (task branch vs. base with diverging changes). |
| `task_dir` | Full scheduler task directory structure (worktree + `env.sh`). |

### Flow Helpers (`tests/integration/flow_helpers.py`)

| Helper | Description |
|---|---|
| `make_task_id()` | Generates a unique task ID (`TEST-{uuid}`). |
| `create_task()` | Creates a task with specified role, branch, type, priority, blocked_by. |
| `create_and_claim()` | Creates then claims a task. Returns `(task_id, claimed_task)`. |
| `create_provisional()` | Creates, claims, and submits a task to reach the `provisional` queue. |

---

## Pytest Markers

Markers are defined in `tests/integration/pytest.ini`.

| Marker | Description |
|---|---|
| `slow` | Long-running tests. Skip with `-m "not slow"`. |
| `integration` | Tests requiring the test server. |
| `api` | API endpoint tests. |
| `lifecycle` | Full task state machine tests. |
| `concurrency` | Race condition and concurrency tests. |

---

## Writing Tests

### Unit Test (mocked SDK)

```python
class TestListTasks:
    def test_returns_incoming_tasks(self, mock_config, mock_sdk_for_unit_tests):
        mock_sdk_for_unit_tests.tasks.list.return_value = [
            {"id": "TASK-1", "queue": "incoming"},
        ]

        from orchestrator.queue_utils import list_tasks
        result = list_tasks("incoming")

        assert len(result) == 1
        mock_sdk_for_unit_tests.tasks.list.assert_called_once_with(queue="incoming")
```

Key points:
- Import the module under test *inside* the test or after patching, so the mock is active.
- Assert on call arguments, not just return values.

### Integration Test (real server)

```python
class TestTaskLifecycle:
    def test_create_claim_submit(self, scoped_sdk, orchestrator_id):
        # Create
        task = scoped_sdk.tasks.create(
            id="TEST-123",
            file_path=".octopoid/tasks/TEST-123.md",
            title="Test task",
            role="implement",
        )
        assert task["queue"] == "incoming"

        # Claim
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed["id"] == "TEST-123"
        assert claimed["queue"] == "claimed"
```

Key points:
- Always use `scoped_sdk`, not the bare `sdk` fixture.
- `orchestrator_id` must be passed so the claim is attributed to a registered orchestrator.
- Each test gets a completely isolated scope; you don't need to clean up after yourself.

### Git Test (real git repo)

```python
import subprocess

class TestMergeConflict:
    def test_detects_conflict(self, conflicting_repo):
        work = conflicting_repo["work"]

        result = subprocess.run(
            ["git", "merge", "task-branch"],
            cwd=work,
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "CONFLICT" in result.stdout
```

Key points:
- Pass `cwd=` to subprocess calls; never `cd` into a directory that might be deleted.
- Use `conflicting_repo["work"]` for the working clone, `conflicting_repo["bare"]` for the remote.

### Scheduler/Backpressure Test (pre-fetched counts)

The scheduler passes pre-fetched queue counts into guard functions so guards don't need to make extra API calls.

```python
class TestBackpressure:
    def test_allows_when_within_limits(self):
        ctx = AgentContext(
            agent_name="test",
            queue_counts={"incoming": 2, "claimed": 0, "provisional": 0},
        )

        with patch("orchestrator.backpressure.count_queue") as mock_count:
            proceed, reason = guard_backpressure(ctx)

        mock_count.assert_not_called()   # No extra API calls
        assert proceed is True
```

---

## Safety Guarantees

The test setup includes multiple layers to prevent accidental production side effects:

1. **Auto-mock in unit tests:** `conftest.py` patches `get_sdk()` before any test module loads.
2. **URL guard in integration tests:** `OCTOPOID_SERVER_URL` is set at module-load time to `http://localhost:9787`. An assertion fires if it ever resolves to a `workers.dev` URL.
3. **Separate database:** The test server uses `octopoid-test` D1 — a completely separate database from production.
4. **Scoped isolation:** `scoped_sdk` namespaces every task under a UUID prefix, so parallel test runs don't interfere.

---

## Troubleshooting

**Integration tests skip or fail with "connection refused"**
The test server is not running. Start it with `./tests/integration/bin/start-test-server.sh` and wait for it to print a healthy status before running tests.

**Tests pass locally but fail in CI**
Check whether the test imports the module at the top of the file before the mock is applied. Move the import inside the test function, or use `importlib.reload()` after patching.

**Unit test makes a real HTTP call**
The `mock_sdk_for_unit_tests` fixture only patches the `get_sdk` path used by `orchestrator` modules. If your code imports from a different path, add a patch in the test or in `conftest.py`.

**Stale bytecode running old code**
After editing `orchestrator/` files, clear the Python bytecode cache so the scheduler and tests see your changes:

```bash
find orchestrator -name '__pycache__' -type d -exec rm -rf {} +
```
