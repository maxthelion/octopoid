"""Pytest configuration and fixtures for integration tests."""

import pytest
from octopoid_sdk import OctopoidSDK
import socket

# Test server URL
TEST_SERVER_URL = "http://localhost:8788"


@pytest.fixture(scope="session")
def test_server_url():
    """URL of the test server."""
    return TEST_SERVER_URL


@pytest.fixture(scope="session")
def sdk():
    """SDK client connected to test server."""
    return OctopoidSDK(server_url=TEST_SERVER_URL)


@pytest.fixture(scope="session")
def orchestrator_id():
    """Orchestrator ID for test claims."""
    return f"test-{socket.gethostname()}"


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
            # Delete test tasks
            if any(task_id.startswith(prefix) for prefix in [
                'test-', 'lifecycle-', 'race-', 'mock-', 'qu-',
                'wrong-queue-', 'unclaimed-', 'lease-', 'multi-', 'reject-'
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
