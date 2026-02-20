"""Smoke tests for tests/fixtures/mock-agent.sh, tests/fixtures/bin/gh,
and the test_repo / conflicting_repo / task_dir pytest fixtures."""

import json
import os
import subprocess
from pathlib import Path

from tests.fixtures.mock_helpers import run_mock_agent

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


# ---------------------------------------------------------------------------
# Stateful mode tests (GH_STATE_FILE)
# ---------------------------------------------------------------------------


def _run_gh_stateful(*args, state_file, env_overrides=None):
    """Run fake gh with GH_STATE_FILE set for stateful mode."""
    overrides = {"GH_STATE_FILE": str(state_file), **(env_overrides or {})}
    return _run_gh(*args, env_overrides=overrides)


def test_fake_gh_stateful_create_view_merge_sequence(tmp_path):
    """Stateful mode: create → view → merge sequence tracks PR state correctly."""
    state_file = tmp_path / "state.json"
    branch = "agent/TASK-abc123"

    # Create PR
    result = _run_gh_stateful(
        "pr", "create", "--base", "main", "--head", branch, "--title", "Test PR", "--body", "body",
        state_file=state_file,
    )
    assert result.returncode == 0, result.stderr
    assert "github.com" in result.stdout
    pr_url = result.stdout.strip()

    # View by branch — should return CLEAN status
    result = _run_gh_stateful(
        "pr", "view", branch, "--json", "mergeable",
        state_file=state_file,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["mergeable"] == "CLEAN"
    assert data["mergeStateStatus"] == "CLEAN"
    pr_number = data["number"]

    # View by number — same PR
    result = _run_gh_stateful(
        "pr", "view", str(pr_number), "--json", "mergeable",
        state_file=state_file,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["number"] == pr_number

    # View with -q to get URL
    result = _run_gh_stateful(
        "pr", "view", branch, "--json", "url", "-q", ".url",
        state_file=state_file,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == pr_url

    # Merge PR
    result = _run_gh_stateful(
        "pr", "merge", str(pr_number), "--squash",
        state_file=state_file,
    )
    assert result.returncode == 0, result.stderr
    assert "Merged" in result.stdout

    # View after merge — status should be MERGED
    result = _run_gh_stateful(
        "pr", "view", branch, "--json", "mergeable",
        state_file=state_file,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["mergeable"] == "MERGED"


def test_fake_gh_stateful_duplicate_create_errors(tmp_path):
    """Stateful mode: creating a PR for the same branch twice returns an error."""
    state_file = tmp_path / "state.json"
    branch = "my-feature"

    # First create succeeds
    result = _run_gh_stateful(
        "pr", "create", "--base", "main", "--head", branch, "--title", "First", "--body", "b",
        state_file=state_file,
    )
    assert result.returncode == 0, result.stderr

    # Second create for same branch fails
    result = _run_gh_stateful(
        "pr", "create", "--base", "main", "--head", branch, "--title", "Duplicate", "--body", "b",
        state_file=state_file,
    )
    assert result.returncode != 0
    assert "already exists" in result.stderr


def test_fake_gh_stateful_view_not_found(tmp_path):
    """Stateful mode: viewing a PR that doesn't exist exits non-zero."""
    state_file = tmp_path / "state.json"

    result = _run_gh_stateful(
        "pr", "view", "no-such-branch", "--json", "mergeable",
        state_file=state_file,
    )
    assert result.returncode != 0


def test_fake_gh_stateful_list_returns_all_prs(tmp_path):
    """Stateful mode: pr list returns all created PRs as a JSON array."""
    state_file = tmp_path / "state.json"

    _run_gh_stateful(
        "pr", "create", "--base", "main", "--head", "branch-one", "--title", "PR 1", "--body", "b",
        state_file=state_file,
    )
    _run_gh_stateful(
        "pr", "create", "--base", "main", "--head", "branch-two", "--title", "PR 2", "--body", "b",
        state_file=state_file,
    )

    result = _run_gh_stateful("pr", "list", state_file=state_file)
    assert result.returncode == 0, result.stderr
    prs = json.loads(result.stdout)
    assert len(prs) == 2
    branches = {pr["headRefName"] for pr in prs}
    assert branches == {"branch-one", "branch-two"}


def test_fake_gh_stateful_merge_fail_honoured(tmp_path):
    """Stateful mode: GH_MOCK_MERGE_FAIL=true still causes pr merge to fail."""
    state_file = tmp_path / "state.json"

    _run_gh_stateful(
        "pr", "create", "--base", "main", "--head", "my-branch", "--title", "T", "--body", "b",
        state_file=state_file,
    )

    result = _run_gh_stateful(
        "pr", "merge", "1", "--squash",
        state_file=state_file,
        env_overrides={"GH_MOCK_MERGE_FAIL": "true"},
    )
    assert result.returncode != 0


def test_fake_gh_stateful_pr_numbers_increment(tmp_path):
    """Stateful mode: each new PR gets a unique incrementing number."""
    state_file = tmp_path / "state.json"

    r1 = _run_gh_stateful(
        "pr", "create", "--base", "main", "--head", "branch-a", "--title", "A", "--body", "b",
        state_file=state_file,
    )
    r2 = _run_gh_stateful(
        "pr", "create", "--base", "main", "--head", "branch-b", "--title", "B", "--body", "b",
        state_file=state_file,
    )
    assert result.returncode == 0 if (result := r1) else True
    url1 = r1.stdout.strip()
    url2 = r2.stdout.strip()
    assert url1 != url2
    # URLs contain sequential numbers
    assert "/pull/1" in url1
    assert "/pull/2" in url2


def test_fake_gh_no_state_file_backwards_compatible():
    """Without GH_STATE_FILE, fake gh behaves exactly as before (stateless)."""
    # pr create returns URL with GH_MOCK_PR_NUMBER
    result = _run_gh(
        "pr", "create", "--base", "main", "--head", "feature", "--title", "T", "--body", "b",
        env_overrides={"GH_MOCK_PR_NUMBER": "77"},
    )
    assert result.returncode == 0
    assert "77" in result.stdout

    # pr view returns JSON from env vars (no state file needed)
    result = _run_gh(
        "pr", "view", "77", "--json", "mergeable",
        env_overrides={"GH_MOCK_PR_NUMBER": "77", "GH_MOCK_MERGE_STATUS": "CLEAN"},
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["number"] == 77
    assert data["mergeable"] == "CLEAN"

    # pr list returns empty array
    result = _run_gh("pr", "list")
    assert result.returncode == 0
    assert json.loads(result.stdout) == []

    # pr merge succeeds
    result = _run_gh("pr", "merge", "77", "--squash")
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Fixture smoke tests: test_repo, conflicting_repo, task_dir, run_mock_agent
# ---------------------------------------------------------------------------


def test_test_repo_has_bare_remote_and_working_copy(test_repo):
    """test_repo fixture creates a bare remote and a working clone with commits."""
    bare = test_repo["bare"]
    work = test_repo["work"]

    # Both paths exist
    assert bare.is_dir()
    assert work.is_dir()

    # Bare repo has the expected git structure
    assert (bare / "HEAD").exists()

    # Working copy has README.md from the initial commit
    assert (work / "README.md").exists()

    # Working copy has at least one commit
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=work,
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.strip() != ""

    # Working copy is connected to the bare remote
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=work,
        capture_output=True,
        text=True,
        check=True,
    )
    assert str(bare) in remote.stdout


def test_conflicting_repo_branches_diverge(conflicting_repo):
    """conflicting_repo fixture produces two branches with different shared-file.txt."""
    work = conflicting_repo["work"]

    # Base branch has its version of the file
    base_content = subprocess.run(
        ["git", "show", "HEAD:shared-file.txt"],
        cwd=work,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "base branch content" in base_content.stdout

    # task-branch has the conflicting version
    task_content = subprocess.run(
        ["git", "show", "task-branch:shared-file.txt"],
        cwd=work,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "task branch content" in task_content.stdout

    # The two branches are different
    assert base_content.stdout != task_content.stdout


def test_task_dir_fixture_creates_proper_structure(task_dir):
    """task_dir fixture creates worktree/ clone and env.sh; result.json absent."""
    assert task_dir.is_dir()
    assert (task_dir / "worktree").is_dir()
    assert (task_dir / "env.sh").exists()
    # result.json must not exist — the agent creates it
    assert not (task_dir / "result.json").exists()

    # worktree is a valid git repo
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=task_dir / "worktree",
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0


def test_run_mock_agent_success_writes_result_json(task_dir):
    """run_mock_agent with success outcome writes {"outcome": "done"} to result.json."""
    result = run_mock_agent(task_dir, agent_env={"MOCK_OUTCOME": "success", "MOCK_COMMITS": "1"})
    assert result.returncode == 0, result.stderr
    result_json = task_dir / "result.json"
    assert result_json.exists()
    data = json.loads(result_json.read_text())
    assert data == {"outcome": "done"}


def test_run_mock_agent_crash_leaves_no_result_json(task_dir):
    """run_mock_agent with MOCK_CRASH=true exits non-zero and leaves no result.json."""
    result = run_mock_agent(task_dir, agent_env={"MOCK_CRASH": "true"})
    assert result.returncode != 0
    assert not (task_dir / "result.json").exists()
