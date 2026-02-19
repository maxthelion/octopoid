"""Smoke tests for tests/fixtures/mock-agent.sh and tests/fixtures/bin/gh."""

import json
import os
import subprocess
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MOCK_AGENT = FIXTURES_DIR / "mock-agent.sh"
FAKE_GH = FIXTURES_DIR / "bin" / "gh"


def _init_git_repo(path: Path) -> None:
    """Initialise a minimal git repo so mock-agent.sh can commit."""
    path.mkdir(exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    (path / "README").write_text("init")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _run_agent(tmp_path: Path, env_overrides: dict) -> subprocess.CompletedProcess:
    worktree = tmp_path / "worktree"
    _init_git_repo(worktree)
    base_env = {
        **os.environ,
        "TASK_WORKTREE": str(worktree),
        "TASK_DIR": str(tmp_path),
        "MOCK_COMMITS": "0",
    }
    base_env.update(env_overrides)
    return subprocess.run(
        [str(MOCK_AGENT)],
        env=base_env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# mock-agent.sh tests
# ---------------------------------------------------------------------------


def test_mock_agent_success(tmp_path):
    """mock-agent.sh writes {"outcome": "done"} for success outcome."""
    result = _run_agent(tmp_path, {"MOCK_OUTCOME": "success"})
    assert result.returncode == 0, result.stderr
    data = json.loads((tmp_path / "result.json").read_text())
    assert data == {"outcome": "done"}


def test_mock_agent_failure(tmp_path):
    """mock-agent.sh writes {"outcome": "failed"} with reason for failure outcome."""
    result = _run_agent(tmp_path, {"MOCK_OUTCOME": "failure", "MOCK_REASON": "something broke"})
    assert result.returncode == 0, result.stderr
    data = json.loads((tmp_path / "result.json").read_text())
    assert data == {"outcome": "failed", "reason": "something broke"}


def test_mock_agent_needs_continuation(tmp_path):
    """mock-agent.sh writes {"outcome": "needs_continuation"} for continuation."""
    result = _run_agent(tmp_path, {"MOCK_OUTCOME": "needs_continuation"})
    assert result.returncode == 0, result.stderr
    data = json.loads((tmp_path / "result.json").read_text())
    assert data == {"outcome": "needs_continuation"}


def test_mock_agent_gatekeeper_approve(tmp_path):
    """mock-agent.sh writes gatekeeper approve result when MOCK_DECISION=approve."""
    result = _run_agent(tmp_path, {"MOCK_DECISION": "approve", "MOCK_COMMENT": "LGTM"})
    assert result.returncode == 0, result.stderr
    data = json.loads((tmp_path / "result.json").read_text())
    assert data == {"status": "success", "decision": "approve", "comment": "LGTM"}


def test_mock_agent_gatekeeper_reject(tmp_path):
    """mock-agent.sh writes gatekeeper reject result when MOCK_DECISION=reject."""
    result = _run_agent(tmp_path, {"MOCK_DECISION": "reject", "MOCK_COMMENT": "needs work"})
    assert result.returncode == 0, result.stderr
    data = json.loads((tmp_path / "result.json").read_text())
    assert data == {"status": "failure", "decision": "reject", "comment": "needs work"}


def test_mock_agent_crash(tmp_path):
    """mock-agent.sh exits non-zero without writing result.json when MOCK_CRASH=true."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    result = subprocess.run(
        [str(MOCK_AGENT)],
        env={**os.environ, "TASK_WORKTREE": str(worktree), "TASK_DIR": str(tmp_path), "MOCK_CRASH": "true"},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not (tmp_path / "result.json").exists()


def test_mock_agent_commits(tmp_path):
    """mock-agent.sh makes the configured number of git commits."""
    worktree = tmp_path / "worktree"
    _init_git_repo(worktree)
    result = subprocess.run(
        [str(MOCK_AGENT)],
        env={
            **os.environ,
            "TASK_WORKTREE": str(worktree),
            "TASK_DIR": str(tmp_path),
            "MOCK_OUTCOME": "success",
            "MOCK_COMMITS": "3",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=worktree, capture_output=True, text=True, check=True,
    )
    # 1 init commit + 3 mock commits
    lines = [l for l in log.stdout.strip().splitlines() if l]
    assert len(lines) == 4


# ---------------------------------------------------------------------------
# tests/fixtures/bin/gh tests
# ---------------------------------------------------------------------------


def _run_gh(*args, env_overrides=None):
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(
        [str(FAKE_GH), *args],
        env=env,
        capture_output=True,
        text=True,
    )


def test_fake_gh_pr_create():
    """Fake gh returns a PR URL containing the configured PR number."""
    result = _run_gh("pr", "create", "--base", "main", "--head", "feature", "--title", "Test", "--body", "body",
                     env_overrides={"GH_MOCK_PR_NUMBER": "42"})
    assert result.returncode == 0
    assert "42" in result.stdout


def test_fake_gh_pr_merge_success():
    """Fake gh succeeds for pr merge when GH_MOCK_MERGE_FAIL=false."""
    result = _run_gh("pr", "merge", "42", "--squash", env_overrides={"GH_MOCK_MERGE_FAIL": "false"})
    assert result.returncode == 0


def test_fake_gh_pr_merge_fail():
    """Fake gh fails for pr merge when GH_MOCK_MERGE_FAIL=true."""
    result = _run_gh("pr", "merge", "42", "--squash", env_overrides={"GH_MOCK_MERGE_FAIL": "true"})
    assert result.returncode != 0


def test_fake_gh_pr_view_returns_mergeable():
    """Fake gh returns JSON with mergeable field for pr view."""
    result = _run_gh("pr", "view", "42", "--json", "mergeable",
                     env_overrides={"GH_MOCK_MERGE_STATUS": "CLEAN", "GH_MOCK_PR_NUMBER": "42"})
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["mergeable"] == "CLEAN"
    assert data["number"] == 42


def test_fake_gh_pr_view_conflicting():
    """Fake gh returns CONFLICTING mergeable status when configured."""
    result = _run_gh("pr", "view", "7", "--json", "mergeable",
                     env_overrides={"GH_MOCK_MERGE_STATUS": "CONFLICTING", "GH_MOCK_PR_NUMBER": "7"})
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["mergeable"] == "CONFLICTING"


def test_fake_gh_pr_view_existing_branch():
    """Fake gh returns PR URL when GH_MOCK_PR_EXISTS=true and -q is used."""
    result = _run_gh("pr", "view", "my-branch", "--json", "url", "-q", ".url",
                     env_overrides={"GH_MOCK_PR_EXISTS": "true", "GH_MOCK_PR_NUMBER": "55"})
    assert result.returncode == 0
    assert "55" in result.stdout


def test_fake_gh_pr_view_no_existing_pr():
    """Fake gh fails when GH_MOCK_PR_EXISTS=false and -q is used."""
    result = _run_gh("pr", "view", "my-branch", "--json", "url", "-q", ".url",
                     env_overrides={"GH_MOCK_PR_EXISTS": "false"})
    assert result.returncode != 0


def test_fake_gh_logs_calls(tmp_path):
    """Fake gh appends all calls to GH_MOCK_LOG when set."""
    log_file = tmp_path / "gh.log"
    _run_gh("pr", "create", "--title", "Test",
            env_overrides={"GH_MOCK_LOG": str(log_file)})
    _run_gh("pr", "merge", "42",
            env_overrides={"GH_MOCK_LOG": str(log_file)})
    assert log_file.exists()
    log_content = log_file.read_text()
    assert "pr create" in log_content
    assert "pr merge" in log_content
