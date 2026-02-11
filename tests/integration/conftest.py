"""Pytest configuration and fixtures for integration tests."""

import pytest
from octopoid_sdk import OctopoidSDK
import socket

# Test server URL
TEST_SERVER_URL = "http://localhost:9787"


@pytest.fixture(scope="session")
def test_server_url():
    """URL of the test server."""
    return TEST_SERVER_URL


@pytest.fixture(scope="session")
def sdk():
    """SDK client connected to test server."""
    return OctopoidSDK(server_url=TEST_SERVER_URL)


@pytest.fixture(scope="session")
def orchestrator_id(test_server_url):
    """Orchestrator ID for test claims - registers orchestrator if needed."""
    import requests

    cluster = "test"
    machine_id = socket.gethostname()
    orch_id = f"{cluster}-{machine_id}"

    # Register orchestrator to satisfy foreign key constraint
    # This is idempotent - re-registration just updates the record
    result = requests.post(
        f"{test_server_url}/api/v1/orchestrators/register",
        json={
            "cluster": cluster,
            "machine_id": machine_id,
            "repo_url": "https://github.com/test/octopoid.git",
            "hostname": socket.gethostname(),
            "version": "2.0.0-test"
        }
    ).json()
    print(f"✓ Registered test orchestrator: {result}")

    return orch_id


@pytest.fixture(scope="function")
def clean_tasks(sdk):
    """Clean all test tasks before and after each test."""
    # Clean before
    _cleanup_test_tasks(sdk)

    yield

    # Clean after
    _cleanup_test_tasks(sdk)


def _cleanup_test_tasks(sdk):
    """Delete all tasks with test IDs."""
    try:
        tasks = sdk.tasks.list()
        for task in tasks:
            task_id = task.get('id', '')
            # Delete test tasks - expanded list of prefixes
            if any(task_id.startswith(prefix) for prefix in [
                'test-', 'lifecycle-', 'race-', 'mock-', 'qu-',
                'wrong-queue-', 'unclaimed-', 'lease-', 'multi-', 'reject-',
                'claim-', 'not-', 'debug-'  # Added claim-, not-, debug-
            ]):
                try:
                    sdk.tasks.delete(task_id)
                except Exception:
                    pass  # Task may already be deleted
    except Exception as e:
        print(f"Warning: Failed to cleanup tasks: {e}")


@pytest.fixture(scope="session", autouse=True)
def verify_test_server():
    """Verify test server is running before tests start."""
    sdk = OctopoidSDK(server_url=TEST_SERVER_URL)
    try:
        health = sdk.status.health()
        assert health['status'] == 'healthy', "Test server is not healthy"
        print(f"\n✓ Test server ready: {health}")
    except Exception as e:
        pytest.exit(
            f"Test server not reachable at {TEST_SERVER_URL}. "
            f"Run: ./tests/integration/bin/start-test-server.sh\n"
            f"Error: {e}"
        )
