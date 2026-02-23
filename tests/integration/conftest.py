"""Pytest configuration and fixtures for integration tests.

IMPORTANT: All tests run against the LOCAL test server (localhost:9787),
never the production API.

Safety layers:
1. OCTOPOID_SERVER_URL is set BEFORE any fixture or import can call get_sdk()
2. The `sdk` fixture creates an OctopoidSDK pointed at TEST_SERVER_URL
3. A guard asserts that no URL containing `workers.dev` is ever used
4. Cleanup deletes all test-prefixed tasks on teardown
"""

import os

# ── STEP 0: Set env var at module level ─────────────────────────────
# This runs at import time (before any session fixture), so get_sdk()
# will always resolve to the local test server.
_ORIGINAL_SERVER_URL = os.environ.get("OCTOPOID_SERVER_URL")
_ORIGINAL_API_KEY = os.environ.get("OCTOPOID_API_KEY")
TEST_SERVER_URL = "http://localhost:9787"
os.environ["OCTOPOID_SERVER_URL"] = TEST_SERVER_URL
# Unset any stale API key — the test server runs without auth, and a
# stale OCTOPOID_API_KEY from the production environment would cause 401s.
os.environ.pop("OCTOPOID_API_KEY", None)

import socket

import pytest
from octopoid_sdk import OctopoidSDK


def _assert_not_production(url: str) -> None:
    """Fail immediately if a URL points at production."""
    if "workers.dev" in url:
        pytest.fail(
            f"SAFETY: refusing to run integration tests against production URL: {url}"
        )


# Guard at import time
_assert_not_production(TEST_SERVER_URL)


# ── Session fixtures ────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_server_url():
    """URL of the test server."""
    return TEST_SERVER_URL


@pytest.fixture(scope="session")
def sdk():
    """SDK client connected to test server (shared session scope)."""
    _assert_not_production(TEST_SERVER_URL)
    return OctopoidSDK(server_url=TEST_SERVER_URL, scope="integration-test")


@pytest.fixture(scope="session", autouse=True)
def isolate_from_production(sdk):
    """Ensure get_sdk() always resolves to the session test SDK.

    The env var is already set at module level, but this fixture also
    replaces the cached SDK inside the sdk module with our session-scoped
    test SDK. This ensures scheduler functions (claim_task, handle_agent_result,
    etc.) use the same scope as the session `sdk` fixture.
    """
    import orchestrator.sdk as sdk_module

    old_sdk = sdk_module._sdk
    sdk_module._sdk = sdk

    # Double-check env var is still pointing at test server
    assert os.environ.get("OCTOPOID_SERVER_URL") == TEST_SERVER_URL

    yield

    # Restore original state
    sdk_module._sdk = old_sdk
    if _ORIGINAL_SERVER_URL is None:
        os.environ.pop("OCTOPOID_SERVER_URL", None)
    else:
        os.environ["OCTOPOID_SERVER_URL"] = _ORIGINAL_SERVER_URL
    if _ORIGINAL_API_KEY is None:
        os.environ.pop("OCTOPOID_API_KEY", None)
    else:
        os.environ["OCTOPOID_API_KEY"] = _ORIGINAL_API_KEY


@pytest.fixture(scope="session", autouse=True)
def verify_test_server():
    """Verify test server is running before tests start."""
    _assert_not_production(TEST_SERVER_URL)
    client = OctopoidSDK(server_url=TEST_SERVER_URL)
    try:
        health = client.status.health()
        assert health['status'] == 'healthy', "Test server is not healthy"
        print(f"\n✓ Test server ready: {health}")
    except Exception as e:
        pytest.skip(
            f"Test server not reachable at {TEST_SERVER_URL}. "
            f"Run: ./tests/integration/bin/start-test-server.sh  "
            f"Error: {e}"
        )


@pytest.fixture(scope="session")
def orchestrator_id(test_server_url):
    """Orchestrator ID for test claims — registers orchestrator if needed."""
    import requests

    _assert_not_production(test_server_url)

    cluster = "test"
    machine_id = socket.gethostname()
    orch_id = f"{cluster}-{machine_id}"

    # Register orchestrator (idempotent)
    result = requests.post(
        f"{test_server_url}/api/v1/orchestrators/register",
        json={
            "cluster": cluster,
            "machine_id": machine_id,
            "repo_url": "https://github.com/test/octopoid.git",
            "hostname": socket.gethostname(),
            "version": "2.0.0-test",
            "scope": "integration-test",
        },
    ).json()
    print(f"✓ Registered test orchestrator: {result}")

    return orch_id


# ── Per-test cleanup ────────────────────────────────────────────────


@pytest.fixture(scope="function")
def clean_tasks(sdk):
    """Delete ALL tasks on the test server before and after each test.

    This is safe because the test server is a disposable local instance.
    Deleting everything avoids stale data from previous runs interfering
    with assertions (e.g. claim returning an old task instead of the
    newly created one).
    """
    _cleanup_all_tasks(sdk)
    yield
    _cleanup_all_tasks(sdk)


def _cleanup_all_tasks(sdk):
    """Delete every task on the test server."""
    try:
        tasks = sdk.tasks.list()
        for task in tasks:
            try:
                sdk.tasks.delete(task["id"])
            except Exception:
                pass  # already deleted
    except Exception as e:
        print(f"Warning: cleanup failed: {e}")


@pytest.fixture
def scoped_sdk(test_server_url):
    """SDK client scoped to this test — complete isolation, no cleanup needed.

    Also patches orchestrator.sdk._sdk so that get_sdk() returns this scoped
    client. This ensures that scheduler functions (handle_agent_result,
    check_and_requeue_expired_leases, etc.) use the same scope as the test.
    """
    import uuid
    import orchestrator.sdk as sdk_module

    scope = f"test-{uuid.uuid4().hex[:8]}"
    client = OctopoidSDK(server_url=test_server_url, scope=scope)

    # Patch get_sdk() to return this scoped client
    old_sdk = sdk_module._sdk
    sdk_module._sdk = client

    yield client

    # Restore
    sdk_module._sdk = old_sdk
    client.close()
