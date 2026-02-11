# Integration Testing Plan - Octopoid v2.0 API-only Architecture

## Overview

Test the complete task lifecycle across API server, SDK, scheduler, and agents with isolated test infrastructure.

## Test Infrastructure

### 1. Test Server Setup

**Separate Wrangler Instance:**
```bash
# packages/server/wrangler.test.toml
name = "octopoid-server-test"
main = "src/index.ts"
compatibility_date = "2024-01-01"

[[d1_databases]]
binding = "DB"
database_name = "octopoid-test"
database_id = "test-db-id"

# Different port to avoid conflicts
[dev]
port = 8788
```

**Test Database:**
```bash
# Create fresh test DB for each run
wrangler d1 create octopoid-test
wrangler d1 migrations apply octopoid-test
```

**Start/Stop Scripts:**
```bash
# tests/integration/bin/start-test-server.sh
#!/bin/bash
cd packages/server
wrangler dev --config wrangler.test.toml --port 8788 > /tmp/octopoid-test-server.log 2>&1 &
echo $! > /tmp/octopoid-test-server.pid

# Wait for server to be ready
for i in {1..30}; do
    curl -s http://localhost:8788/api/health && break
    sleep 1
done
```

```bash
# tests/integration/bin/stop-test-server.sh
#!/bin/bash
if [ -f /tmp/octopoid-test-server.pid ]; then
    kill $(cat /tmp/octopoid-test-server.pid)
    rm /tmp/octopoid-test-server.pid
fi
```

### 2. Test Configuration

**Test SDK Config:**
```yaml
# tests/integration/fixtures/test-config.yaml
server:
  enabled: true
  url: http://localhost:8788
  cluster: test
  machine_id: test-orchestrator-1

database:
  enabled: false  # v2.0 is API-only

agents:
  max_concurrent: 2
```

### 3. Test Fixtures

**Task Fixtures:**
```python
# tests/integration/fixtures/tasks.py
from datetime import datetime
from uuid import uuid4

def create_test_task(
    title="Test Task",
    role="implement",
    priority="P1",
    **kwargs
):
    """Create a test task fixture."""
    task_id = kwargs.get('id', f"test-{uuid4().hex[:8]}")

    return {
        "id": task_id,
        "title": title,
        "role": role,
        "priority": priority,
        "queue": kwargs.get("queue", "incoming"),
        "branch": kwargs.get("branch", "main"),
        "created_at": datetime.now().isoformat(),
        "context": kwargs.get("context", "Test context"),
        "acceptance_criteria": kwargs.get("acceptance_criteria", "- [ ] Test passes"),
        **kwargs
    }
```

## Test Suites

### Suite 1: API Server Tests

**Test File:** `tests/integration/test_api_server.py`

```python
import pytest
from octopoid_sdk import OctopoidSDK

@pytest.fixture
def sdk():
    """Test SDK connected to test server."""
    return OctopoidSDK(server_url="http://localhost:8788")

class TestServerHealth:
    def test_health_endpoint(self, sdk):
        """Server responds to health check."""
        health = sdk.status.health()
        assert health['status'] == 'healthy'
        assert health['database'] == 'connected'

class TestTaskCRUD:
    def test_create_task(self, sdk):
        """Create task via API."""
        task = sdk.tasks.create(
            id="test-001",
            file_path="/tmp/test-001.md",
            title="Test Task",
            role="implement",
            priority="P1",
            queue="incoming"
        )
        assert task['id'] == "test-001"
        assert task['queue'] == "incoming"

    def test_list_tasks(self, sdk):
        """List tasks from API."""
        # Create tasks
        sdk.tasks.create(id="test-002", file_path="/tmp/test-002.md",
                         title="Task 2", role="implement")
        sdk.tasks.create(id="test-003", file_path="/tmp/test-003.md",
                         title="Task 3", role="implement")

        # List all
        tasks = sdk.tasks.list()
        assert len(tasks) >= 2

        # List by queue
        incoming = sdk.tasks.list(queue="incoming")
        assert all(t['queue'] == 'incoming' for t in incoming)

    def test_update_task(self, sdk):
        """Update task via API."""
        task = sdk.tasks.create(id="test-004", file_path="/tmp/test-004.md",
                                title="Old Title", role="implement")

        updated = sdk.tasks.update("test-004", title="New Title", priority="P0")
        assert updated['title'] == "New Title"
        assert updated['priority'] == "P0"

    def test_delete_task(self, sdk):
        """Delete task via API."""
        sdk.tasks.create(id="test-005", file_path="/tmp/test-005.md",
                        title="To Delete", role="implement")

        result = sdk.tasks.delete("test-005")
        assert result['message'] == 'Task deleted'

        # Verify deletion
        task = sdk.tasks.get("test-005")
        assert task is None
```

### Suite 2: Task Lifecycle Tests

**Test File:** `tests/integration/test_task_lifecycle.py`

```python
import pytest
from octopoid_sdk import OctopoidSDK
import socket

@pytest.fixture
def sdk():
    return OctopoidSDK(server_url="http://localhost:8788")

@pytest.fixture
def orchestrator_id():
    return socket.gethostname()

class TestBasicLifecycle:
    def test_create_claim_submit_accept(self, sdk, orchestrator_id):
        """Full task lifecycle: create → claim → submit → accept → done."""

        # 1. Create task
        task = sdk.tasks.create(
            id="lifecycle-001",
            file_path="/tmp/lifecycle-001.md",
            title="Lifecycle Test",
            role="implement",
            priority="P1"
        )
        assert task['queue'] == 'incoming'

        # 2. Claim task
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement"
        )
        assert claimed is not None
        assert claimed['id'] == "lifecycle-001"
        assert claimed['queue'] == 'claimed'
        assert claimed['claimed_by'] == 'test-agent'

        # 3. Submit completion
        submitted = sdk.tasks.submit(
            task_id="lifecycle-001",
            commits_count=3,
            turns_used=5
        )
        assert submitted['queue'] == 'provisional'
        assert submitted['commits_count'] == 3

        # 4. Accept
        accepted = sdk.tasks.accept(
            task_id="lifecycle-001",
            accepted_by="test-gatekeeper"
        )
        assert accepted['queue'] == 'done'
        assert accepted['accepted_by'] == 'test-gatekeeper'

    def test_claim_submit_reject_retry(self, sdk, orchestrator_id):
        """Rejection flow: create → claim → submit → reject → incoming."""

        # Create and claim
        sdk.tasks.create(id="reject-001", file_path="/tmp/reject-001.md",
                        title="Reject Test", role="implement")
        claimed = sdk.tasks.claim(orchestrator_id=orchestrator_id,
                                  agent_name="test-agent",
                                  role_filter="implement")

        # Submit
        sdk.tasks.submit("reject-001", commits_count=1, turns_used=2)

        # Reject
        rejected = sdk.tasks.reject(
            task_id="reject-001",
            reason="Tests failed",
            rejected_by="test-gatekeeper"
        )
        assert rejected['queue'] == 'incoming'
        assert rejected['rejection_count'] == 1

        # Can be claimed again
        reclaimed = sdk.tasks.claim(orchestrator_id=orchestrator_id,
                                     agent_name="test-agent-2",
                                     role_filter="implement")
        assert reclaimed['id'] == "reject-001"

class TestStateValidation:
    def test_cannot_claim_from_wrong_queue(self, sdk, orchestrator_id):
        """Cannot claim task that's not in incoming queue."""
        # Create task in provisional queue
        sdk.tasks.create(id="wrong-queue-001", file_path="/tmp/wrong-queue.md",
                        title="Wrong Queue", role="implement", queue="provisional")

        # Attempt claim (should find nothing)
        claimed = sdk.tasks.claim(orchestrator_id=orchestrator_id,
                                  agent_name="test-agent",
                                  role_filter="implement")
        # Should return None or different task, not wrong-queue-001
        if claimed:
            assert claimed['id'] != "wrong-queue-001"

    def test_cannot_submit_unclaimed_task(self, sdk):
        """Cannot submit task that wasn't claimed."""
        sdk.tasks.create(id="unclaimed-001", file_path="/tmp/unclaimed.md",
                        title="Unclaimed", role="implement")

        with pytest.raises(Exception):  # Should raise error
            sdk.tasks.submit("unclaimed-001", commits_count=1, turns_used=1)
```

### Suite 3: Concurrency Tests

**Test File:** `tests/integration/test_concurrency.py`

```python
import pytest
from octopoid_sdk import OctopoidSDK
import socket
import threading
import time

@pytest.fixture
def sdk():
    return OctopoidSDK(server_url="http://localhost:8788")

class TestRaceConditions:
    def test_multiple_agents_claim_same_task(self, sdk):
        """Only one agent can claim a task."""
        orchestrator_id = socket.gethostname()

        # Create one task
        sdk.tasks.create(id="race-001", file_path="/tmp/race-001.md",
                        title="Race Test", role="implement")

        results = []

        def attempt_claim(agent_name):
            try:
                task = sdk.tasks.claim(
                    orchestrator_id=orchestrator_id,
                    agent_name=agent_name,
                    role_filter="implement"
                )
                results.append((agent_name, task))
            except Exception as e:
                results.append((agent_name, None))

        # Spawn 5 threads trying to claim
        threads = []
        for i in range(5):
            t = threading.Thread(target=attempt_claim, args=(f"agent-{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Exactly one should succeed
        successful_claims = [r for r in results if r[1] is not None and r[1]['id'] == 'race-001']
        assert len(successful_claims) == 1

    def test_lease_expiration(self, sdk, orchestrator_id):
        """Expired leases allow re-claiming."""
        # Create task
        sdk.tasks.create(id="lease-001", file_path="/tmp/lease-001.md",
                        title="Lease Test", role="implement")

        # Claim with short lease (1 second)
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="agent-1",
            role_filter="implement",
            lease_duration_seconds=1
        )
        assert claimed['id'] == "lease-001"

        # Wait for lease to expire
        time.sleep(2)

        # Should be claimable again
        reclaimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="agent-2",
            role_filter="implement"
        )
        assert reclaimed['id'] == "lease-001"
        assert reclaimed['claimed_by'] == "agent-2"
```

### Suite 4: Mock Agent Tests

**Test File:** `tests/integration/test_mock_agents.py`

```python
import pytest
from octopoid_sdk import OctopoidSDK
import socket
import subprocess
import os
from pathlib import Path

@pytest.fixture
def sdk():
    return OctopoidSDK(server_url="http://localhost:8788")

class MockAgent:
    """Minimal test agent that claims and submits."""

    def __init__(self, agent_name, role="implement"):
        self.agent_name = agent_name
        self.role = role
        self.sdk = OctopoidSDK(server_url="http://localhost:8788")
        self.orchestrator_id = socket.gethostname()

    def run_once(self):
        """Claim one task, do fake work, submit."""
        # Claim
        task = self.sdk.tasks.claim(
            orchestrator_id=self.orchestrator_id,
            agent_name=self.agent_name,
            role_filter=self.role
        )

        if not task:
            return None

        # Simulate work
        import time
        time.sleep(0.1)

        # Submit
        self.sdk.tasks.submit(
            task_id=task['id'],
            commits_count=2,
            turns_used=3
        )

        return task['id']

class TestMockAgents:
    def test_single_agent_claims_and_submits(self, sdk):
        """Mock agent can claim and submit task."""
        # Create task
        sdk.tasks.create(id="mock-001", file_path="/tmp/mock-001.md",
                        title="Mock Test", role="implement")

        # Run agent
        agent = MockAgent("test-implementer")
        task_id = agent.run_once()

        assert task_id == "mock-001"

        # Verify task is now provisional
        task = sdk.tasks.get("mock-001")
        assert task['queue'] == 'provisional'

    def test_multiple_agents_process_queue(self, sdk):
        """Multiple agents process tasks from queue."""
        # Create 10 tasks
        for i in range(10):
            sdk.tasks.create(
                id=f"multi-{i:03d}",
                file_path=f"/tmp/multi-{i:03d}.md",
                title=f"Multi Task {i}",
                role="implement"
            )

        # Run 3 agents in parallel
        import threading
        results = []

        def run_agent(agent_name):
            agent = MockAgent(agent_name)
            claimed = []
            while True:
                task_id = agent.run_once()
                if not task_id:
                    break
                claimed.append(task_id)
            results.append((agent_name, claimed))

        threads = []
        for i in range(3):
            t = threading.Thread(target=run_agent, args=(f"agent-{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All tasks should be claimed
        all_claimed = []
        for agent_name, claimed in results:
            all_claimed.extend(claimed)

        assert len(all_claimed) == 10
        assert len(set(all_claimed)) == 10  # No duplicates
```

### Suite 5: Queue Utils Integration Tests

**Test File:** `tests/integration/test_queue_utils.py`

```python
import pytest
import sys
from pathlib import Path

# Add orchestrator to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "orchestrator"))

from orchestrator import queue_utils

@pytest.fixture(autouse=True)
def setup_test_config(tmp_path):
    """Create test config pointing to test server."""
    config_dir = tmp_path / ".octopoid"
    config_dir.mkdir()

    config_path = config_dir / "config.yaml"
    config_path.write_text("""
server:
  enabled: true
  url: http://localhost:8788
  cluster: test
  machine_id: test-1

database:
  enabled: false
""")

    # Monkey-patch config loading
    original_get_orchestrator_dir = queue_utils.get_orchestrator_dir
    queue_utils.get_orchestrator_dir = lambda: tmp_path / ".orchestrator"

    # Create orchestrator dir
    (tmp_path / ".orchestrator").mkdir()
    (tmp_path / ".octopoid").mkdir(exist_ok=True)

    # Point to test config
    import os
    os.environ['OCTOPOID_CONFIG'] = str(config_path)

    yield

    # Cleanup
    queue_utils.get_orchestrator_dir = original_get_orchestrator_dir
    del os.environ['OCTOPOID_CONFIG']

class TestQueueUtilsIntegration:
    def test_list_tasks(self):
        """queue_utils.list_tasks queries API."""
        tasks = queue_utils.list_tasks("incoming")
        assert isinstance(tasks, list)

    def test_claim_task(self):
        """queue_utils.claim_task uses SDK."""
        # Create task via SDK first
        from octopoid_sdk import OctopoidSDK
        sdk = OctopoidSDK(server_url="http://localhost:8788")
        sdk.tasks.create(id="qu-001", file_path="/tmp/qu-001.md",
                        title="QU Test", role="implement")

        # Claim via queue_utils
        task = queue_utils.claim_task(role_filter="implement", agent_name="test-agent")
        assert task is not None
        assert task['id'] == "qu-001"
```

## Test Runner

**Main Test Script:** `tests/integration/run_tests.sh`

```bash
#!/bin/bash
set -e

echo "=== Starting Integration Tests ==="

# 1. Start test server
echo "Starting test server..."
./tests/integration/bin/start-test-server.sh

# 2. Wait for server
echo "Waiting for server to be ready..."
sleep 2

# 3. Run tests
echo "Running test suites..."
pytest tests/integration/ -v --tb=short

# 4. Stop server
echo "Stopping test server..."
./tests/integration/bin/stop-test-server.sh

echo "=== Tests Complete ==="
```

## CI/CD Integration

**GitHub Actions:** `.github/workflows/integration-tests.yml`

```yaml
name: Integration Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v3

      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: '18'

      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          npm install -g pnpm wrangler
          pnpm install
          pip install pytest octopoid-sdk

      - name: Run integration tests
        run: |
          chmod +x tests/integration/run_tests.sh
          ./tests/integration/run_tests.sh
```

## Test Data Management

**Setup/Teardown:**
```python
# tests/integration/conftest.py
import pytest
from octopoid_sdk import OctopoidSDK

@pytest.fixture(scope="session", autouse=True)
def clean_test_db():
    """Clean test database before and after all tests."""
    sdk = OctopoidSDK(server_url="http://localhost:8788")

    # Clean before
    tasks = sdk.tasks.list()
    for task in tasks:
        if task['id'].startswith('test-') or task['id'].startswith('lifecycle-'):
            sdk.tasks.delete(task['id'])

    yield

    # Clean after
    tasks = sdk.tasks.list()
    for task in tasks:
        if task['id'].startswith('test-') or task['id'].startswith('lifecycle-'):
            sdk.tasks.delete(task['id'])
```

## Summary

**Test Coverage:**
1. ✅ API Server endpoints (CRUD, health)
2. ✅ Task lifecycle (create → claim → submit → accept/reject)
3. ✅ State machine transitions
4. ✅ Concurrency and race conditions
5. ✅ Lease management and expiration
6. ✅ Mock agents (claim/submit)
7. ✅ Queue utils integration with SDK

**Infrastructure:**
- Isolated test server (port 8788)
- Fresh test database per run
- Automated setup/teardown
- CI/CD ready

**Next Steps:**
1. Implement test infrastructure scripts
2. Create pytest test files
3. Add fixtures and helpers
4. Run initial test suite
5. Fix any issues revealed by tests
6. Add to CI/CD pipeline
