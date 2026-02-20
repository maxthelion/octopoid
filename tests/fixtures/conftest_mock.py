"""Pytest fixtures for local git repos and mock task directory structures."""

import subprocess
from pathlib import Path

import pytest


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a git command in a directory, raising on failure."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def test_repo(tmp_path: Path) -> dict:
    """Create a local git repo with a bare remote (no GitHub needed).

    Returns:
        dict with keys:
            "bare": Path to the bare remote repo
            "work": Path to the working clone
    """
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"

    # Init bare repo
    subprocess.run(
        ["git", "init", "--bare", str(bare)],
        check=True,
        capture_output=True,
    )

    # Clone to working copy
    subprocess.run(
        ["git", "clone", str(bare), str(work)],
        check=True,
        capture_output=True,
    )

    # Configure git user in working copy
    _git(["config", "user.email", "test@example.com"], work)
    _git(["config", "user.name", "Test"], work)

    # Seed with initial commit on base branch
    (work / "README.md").write_text("# Test Repo\n")
    _git(["add", "README.md"], work)
    _git(["commit", "-m", "init: add README"], work)
    _git(["push", "origin", "HEAD"], work)

    return {"bare": bare, "work": work}


@pytest.fixture
def conflicting_repo(test_repo: dict) -> dict:
    """Build on test_repo — set up a repo where the task branch conflicts with base.

    State after fixture:
    - task-branch: has a commit changing shared-file.txt
    - base branch: has a conflicting commit on the same file

    Returns the same dict as test_repo.
    """
    work = test_repo["work"]

    # Determine current base branch name
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    base_branch = result.stdout.strip()

    # Create task branch with a change to shared-file.txt
    _git(["checkout", "-b", "task-branch"], work)
    (work / "shared-file.txt").write_text("task branch content\n")
    _git(["add", "shared-file.txt"], work)
    _git(["commit", "-m", "task: change shared-file.txt"], work)
    _git(["push", "origin", "task-branch"], work)

    # Switch back to base and make a conflicting change to the same file
    _git(["checkout", base_branch], work)
    (work / "shared-file.txt").write_text("base branch content\n")
    _git(["add", "shared-file.txt"], work)
    _git(["commit", "-m", "base: conflicting change to shared-file.txt"], work)
    _git(["push", "origin", base_branch], work)

    return test_repo


@pytest.fixture
def task_dir(test_repo: dict, tmp_path: Path) -> Path:
    """Create a mock task directory structure matching what the scheduler creates.

    Structure:
        task_dir/
            worktree/   — clone from test_repo's bare remote
            env.sh      — minimal env file

    Returns:
        Path to the task directory.
    """
    task = tmp_path / "task"
    worktree = task / "worktree"
    task.mkdir()

    # Clone from the bare remote into worktree
    subprocess.run(
        ["git", "clone", str(test_repo["bare"]), str(worktree)],
        check=True,
        capture_output=True,
    )

    # Configure git user in worktree so commits work
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=worktree,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=worktree,
        check=True,
        capture_output=True,
    )

    # Minimal env file
    (task / "env.sh").write_text("export TASK_ID=TASK-test\n")

    # result.json does not exist yet — the agent creates it
    return task
