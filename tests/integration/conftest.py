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
import subprocess
from pathlib import Path

# ── STEP 0: Set env var at module level ─────────────────────────────
# This runs at import time (before any session fixture), so get_sdk()
# will always resolve to the local test server.
_ORIGINAL_SERVER_URL = os.environ.get("OCTOPOID_SERVER_URL")
TEST_SERVER_URL = "http://localhost:9787"
os.environ["OCTOPOID_SERVER_URL"] = TEST_SERVER_URL

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
    """SDK client connected to test server."""
    _assert_not_production(TEST_SERVER_URL)
    return OctopoidSDK(server_url=TEST_SERVER_URL)


@pytest.fixture(scope="session", autouse=True)
def isolate_from_production():
    """Ensure get_sdk() always resolves to the local test server.

    The env var is already set at module level, but this fixture also
    clears the cached SDK inside the sdk module so any prior import cannot
    leak a production SDK into test code.
    """
    import orchestrator.sdk as sdk_module

    # Force-clear the cached SDK so get_sdk() re-initialises with env var
    old_sdk = sdk_module._sdk
    sdk_module._sdk = None

    # Double-check env var is still pointing at test server
    assert os.environ.get("OCTOPOID_SERVER_URL") == TEST_SERVER_URL

    yield

    # Restore original state
    sdk_module._sdk = old_sdk
    if _ORIGINAL_SERVER_URL is None:
        os.environ.pop("OCTOPOID_SERVER_URL", None)
    else:
        os.environ["OCTOPOID_SERVER_URL"] = _ORIGINAL_SERVER_URL


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
    """SDK client scoped to this test — complete isolation, no cleanup needed."""
    import uuid
    scope = f"test-{uuid.uuid4().hex[:8]}"
    client = OctopoidSDK(server_url=test_server_url, scope=scope)
    yield client
    client.close()


# ── Mock agent fixtures (from tests/fixtures/) ──────────────────────

# Absolute path to the shared fixtures directory
_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def test_repo(tmp_path):
    """Create a bare remote git repo and a working clone, seeded with an initial commit.

    Returns a dict with:
        remote: Path to the bare repository (acts as the remote)
        work:   Path to the working clone (has origin pointing at remote)
    """
    remote = tmp_path / "remote.git"
    remote.mkdir()
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)

    work = tmp_path / "repo"
    subprocess.run(
        ["git", "clone", str(remote), str(work)],
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "config", "user.email", "test@test.local"],
        cwd=work, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=work, check=True, capture_output=True,
    )

    (work / "README.md").write_text("# Test repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=work, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=work, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=work, check=True, capture_output=True,
    )

    return {"remote": remote, "work": work}


@pytest.fixture
def run_mock_agent():
    """Return a callable that runs mock-agent.sh with controlled environment.

    The callable signature is::

        run_mock_agent(task_dir, agent_env=None, gh_env=None) -> CompletedProcess

    Args:
        task_dir:   Directory containing a ``worktree/`` subdirectory.
                    ``result.json`` will be written here.
        agent_env:  MOCK_* env vars to pass to mock-agent.sh.
        gh_env:     GH_MOCK_* env vars to pass (used when tests call gh indirectly).

    Returns:
        subprocess.CompletedProcess from running mock-agent.sh.
    """
    mock_agent = _FIXTURES_DIR / "mock-agent.sh"
    mock_bin = _FIXTURES_DIR / "bin"

    def _run(
        task_dir: Path,
        agent_env: dict | None = None,
        gh_env: dict | None = None,
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["RESULT_FILE"] = str(task_dir / "result.json")
        env["PATH"] = f"{mock_bin}:{env.get('PATH', '')}"
        if agent_env:
            env.update(agent_env)
        if gh_env:
            env.update(gh_env)

        return subprocess.run(
            [str(mock_agent)],
            cwd=str(task_dir / "worktree"),
            env=env,
            capture_output=True,
            text=True,
        )

    return _run
