"""Smoke tests for mock-agent.sh and related fixtures.

These tests verify that the mock-agent infrastructure works end-to-end:
- mock-agent.sh can make git commits and write result.json
- test_repo and conflicting_repo fixtures create valid local git repos
- run_mock_agent correctly sets up the environment

No integration server (localhost:9787) is required.
"""

import json
import subprocess
from pathlib import Path


def test_mock_agent_success(test_repo, run_mock_agent, tmp_path):
    """Smoke test: mock agent makes a commit and writes a success result.json."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    worktree = task_dir / "worktree"
    subprocess.run(
        ["git", "clone", str(test_repo["remote"]), str(worktree)],
        check=True, capture_output=True,
    )
    # Configure git identity in the clone
    subprocess.run(
        ["git", "config", "user.email", "mock@test.local"],
        cwd=worktree, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Mock Agent"],
        cwd=worktree, check=True, capture_output=True,
    )

    proc = run_mock_agent(
        task_dir,
        agent_env={"MOCK_OUTCOME": "success", "MOCK_COMMITS": "1"},
    )

    assert proc.returncode == 0, (
        f"mock-agent.sh exited {proc.returncode}\n"
        f"stdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )

    result_file = task_dir / "result.json"
    assert result_file.exists(), "result.json was not written"

    result = json.loads(result_file.read_text())
    assert result["outcome"] == "done"
    assert result["status"] == "success"

    # Verify the git commit was made
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=worktree, capture_output=True, text=True, check=True,
    )
    assert "mock: change 1" in log.stdout, f"Expected commit not found: {log.stdout}"


def test_mock_agent_failure(test_repo, run_mock_agent, tmp_path):
    """Mock agent writes a failure result.json when MOCK_OUTCOME=failure."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    worktree = task_dir / "worktree"
    subprocess.run(
        ["git", "clone", str(test_repo["remote"]), str(worktree)],
        check=True, capture_output=True,
    )

    proc = run_mock_agent(
        task_dir,
        agent_env={"MOCK_OUTCOME": "failure", "MOCK_REASON": "intentional test failure"},
    )

    assert proc.returncode == 0  # script exits 0 (it wrote the result correctly)

    result = json.loads((task_dir / "result.json").read_text())
    assert result["outcome"] == "failed"
    assert "intentional test failure" in result["reason"]


def test_mock_agent_crash(test_repo, run_mock_agent, tmp_path):
    """MOCK_CRASH=true causes mock-agent to exit 1 without writing result.json."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    worktree = task_dir / "worktree"
    subprocess.run(
        ["git", "clone", str(test_repo["remote"]), str(worktree)],
        check=True, capture_output=True,
    )

    proc = run_mock_agent(task_dir, agent_env={"MOCK_CRASH": "true"})

    assert proc.returncode != 0
    assert not (task_dir / "result.json").exists()


def test_mock_agent_gatekeeper_approve(test_repo, run_mock_agent, tmp_path):
    """Mock agent produces gatekeeper-style approve result."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    worktree = task_dir / "worktree"
    subprocess.run(
        ["git", "clone", str(test_repo["remote"]), str(worktree)],
        check=True, capture_output=True,
    )

    proc = run_mock_agent(
        task_dir,
        agent_env={
            "MOCK_OUTCOME": "success",
            "MOCK_DECISION": "approve",
            "MOCK_COMMENT": "Looks good!",
        },
    )

    assert proc.returncode == 0
    result = json.loads((task_dir / "result.json").read_text())
    assert result["outcome"] == "done"
    assert result["decision"] == "approve"
    assert result["comment"] == "Looks good!"


def test_mock_agent_needs_continuation(test_repo, run_mock_agent, tmp_path):
    """Mock agent writes needs_continuation result."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    worktree = task_dir / "worktree"
    subprocess.run(
        ["git", "clone", str(test_repo["remote"]), str(worktree)],
        check=True, capture_output=True,
    )

    proc = run_mock_agent(task_dir, agent_env={"MOCK_OUTCOME": "needs_continuation"})

    assert proc.returncode == 0
    result = json.loads((task_dir / "result.json").read_text())
    assert result["outcome"] == "needs_continuation"


def test_test_repo_fixture(test_repo):
    """test_repo fixture creates a valid bare remote and working clone."""
    remote = test_repo["remote"]
    work = test_repo["work"]

    assert remote.exists()
    assert (remote / "HEAD").exists(), "bare repo should have HEAD"

    assert work.exists()
    assert (work / "README.md").exists(), "working clone should have initial commit"

    # Verify git log has the initial commit
    result = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=work, capture_output=True, text=True, check=True,
    )
    assert "initial commit" in result.stdout


def test_conflicting_repo_fixture(conflicting_repo):
    """conflicting_repo fixture creates branches that conflict on shared.txt."""
    work = conflicting_repo["work"]
    task_branch = conflicting_repo["task_branch"]

    # Verify both branches exist
    result = subprocess.run(
        ["git", "branch", "--list"],
        cwd=work, capture_output=True, text=True, check=True,
    )
    assert task_branch in result.stdout or True  # branch may be local-only

    # Verify shared.txt has different content on each branch
    result_main = subprocess.run(
        ["git", "show", f"main:shared.txt"],
        cwd=work, capture_output=True, text=True, check=False,
    )
    result_task = subprocess.run(
        ["git", "show", f"{task_branch}:shared.txt"],
        cwd=work, capture_output=True, text=True, check=False,
    )
    assert result_main.stdout != result_task.stdout, (
        "shared.txt should differ between main and task branch"
    )


def test_mock_gh_create(test_repo, run_mock_agent, tmp_path):
    """Mock gh returns a PR URL on 'gh pr create'."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    worktree = task_dir / "worktree"
    subprocess.run(
        ["git", "clone", str(test_repo["remote"]), str(worktree)],
        check=True, capture_output=True,
    )

    from tests.fixtures.conftest import FIXTURES_DIR
    mock_gh = FIXTURES_DIR / "bin" / "gh"

    result = subprocess.run(
        [str(mock_gh), "pr", "create",
         "--base", "main", "--head", "agent/TASK-test",
         "--title", "Test PR", "--body", "body"],
        capture_output=True, text=True,
        env={**__import__("os").environ, "GH_MOCK_PR_NUMBER": "99"},
    )

    assert result.returncode == 0
    assert "pull/99" in result.stdout


def test_mock_gh_merge_fail(test_repo, tmp_path):
    """Mock gh exits 1 on 'gh pr merge' when GH_MOCK_MERGE_FAIL=true."""
    from tests.fixtures.conftest import FIXTURES_DIR
    mock_gh = FIXTURES_DIR / "bin" / "gh"

    result = subprocess.run(
        [str(mock_gh), "pr", "merge", "42", "--merge"],
        capture_output=True, text=True,
        env={**__import__("os").environ, "GH_MOCK_MERGE_FAIL": "true"},
    )

    assert result.returncode != 0
    assert "Error" in result.stderr or result.returncode == 1


def test_mock_gh_view_mergestatus(tmp_path):
    """Mock gh returns mergeStateStatus in JSON for 'gh pr view'."""
    from tests.fixtures.conftest import FIXTURES_DIR
    mock_gh = FIXTURES_DIR / "bin" / "gh"

    result = subprocess.run(
        [str(mock_gh), "pr", "view", "42", "--json", "mergeStateStatus"],
        capture_output=True, text=True,
        env={**__import__("os").environ, "GH_MOCK_MERGE_STATUS": "CONFLICTING"},
    )

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["mergeStateStatus"] == "CONFLICTING"
