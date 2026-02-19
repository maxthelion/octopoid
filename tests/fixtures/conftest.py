"""Pytest fixtures for mock-agent and local git repo tests.

These fixtures do NOT require the integration test server (localhost:9787).
They create temporary git repos and run mock-agent.sh in isolation.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

# Absolute path to the fixtures directory (where this conftest lives)
FIXTURES_DIR = Path(__file__).parent


@pytest.fixture
def test_repo(tmp_path):
    """Create a bare remote git repo and a working clone, seeded with an initial commit.

    Returns a dict with:
        remote: Path to the bare repository (acts as the remote)
        work:   Path to the working clone (has origin pointing at remote)
    """
    # Bare remote
    remote = tmp_path / "remote.git"
    remote.mkdir()
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)

    # Working clone
    work = tmp_path / "repo"
    subprocess.run(
        ["git", "clone", str(remote), str(work)],
        check=True,
        capture_output=True,
    )

    # Configure git identity in the clone (needed for commits)
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"],
        cwd=work, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=work, check=True, capture_output=True,
    )

    # Initial commit so the repo has a valid HEAD
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
def conflicting_repo(test_repo):
    """Extend test_repo with a task branch that conflicts with main.

    Creates two branches that both modify shared.txt differently so that
    merging or rebasing the task branch onto main produces a conflict.

    Returns the same dict as test_repo plus:
        task_branch: name of the conflicting branch
    """
    work = test_repo["work"]

    # Add shared.txt on main and push
    (work / "shared.txt").write_text("main content\n")
    subprocess.run(["git", "add", "shared.txt"], cwd=work, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add shared.txt on main"],
        cwd=work, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=work, check=True, capture_output=True,
    )

    # Record the commit before shared.txt was added
    result = subprocess.run(
        ["git", "rev-parse", "HEAD~1"],
        cwd=work, capture_output=True, text=True, check=True,
    )
    base_sha = result.stdout.strip()

    # Create a task branch rooted at the commit before shared.txt
    task_branch = "agent/TASK-conflict-test"
    subprocess.run(
        ["git", "checkout", "-b", task_branch, base_sha],
        cwd=work, check=True, capture_output=True,
    )
    (work / "shared.txt").write_text("task branch content\n")
    subprocess.run(["git", "add", "shared.txt"], cwd=work, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add conflicting shared.txt"],
        cwd=work, check=True, capture_output=True,
    )

    # Return to main so the work dir is in a clean state
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=work, check=True, capture_output=True,
    )

    return {**test_repo, "task_branch": task_branch}


@pytest.fixture
def run_mock_agent():
    """Return a callable that runs mock-agent.sh with controlled environment.

    Usage::

        def test_example(test_repo, run_mock_agent, tmp_path):
            task_dir = tmp_path / "task"
            task_dir.mkdir()
            worktree = task_dir / "worktree"
            subprocess.run(["git", "clone", str(test_repo["remote"]), str(worktree)], check=True)

            proc = run_mock_agent(
                task_dir,
                agent_env={"MOCK_OUTCOME": "success", "MOCK_COMMITS": "1"},
            )
            assert proc.returncode == 0
            result = json.loads((task_dir / "result.json").read_text())
            assert result["outcome"] == "done"

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
    mock_agent = FIXTURES_DIR / "mock-agent.sh"
    mock_bin = FIXTURES_DIR / "bin"

    def _run(
        task_dir: Path,
        agent_env: dict | None = None,
        gh_env: dict | None = None,
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        # Point result.json at task_dir (not task_dir/worktree/../result.json)
        env["RESULT_FILE"] = str(task_dir / "result.json")
        # Prepend mock bin/ so scripts that call "gh" get the mock
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
