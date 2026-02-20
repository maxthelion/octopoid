"""Integration tests for scheduler lifecycle using mock agents.

Exercises real scheduler functions (handle_agent_result,
handle_agent_result_via_flow) with deterministic mock agents, against
a real local server (port 9787).

No Claude API calls. No real GitHub API calls (uses fake gh CLI).

Run with a local server on port 9787:
    cd submodules/server && npx wrangler dev --port 9787

Implementation notes:
- On macOS, BSD `seq 1 0` outputs "1" and "0" (counting down), so the mock
  agent's git loop always executes at least once regardless of MOCK_COMMITS.
  Every TASK_WORKTREE must therefore be a real git repo.
- For implementer tests the worktree must be left in detached HEAD after the
  mock agent commits, so that push_branch can create the task-specific branch.
- Gatekeeper tests use a separate git repo for TASK_WORKTREE (where the mock
  agent commits) and a plain directory for task_dir (where result.json lands).
  The gatekeeper step functions (post_review_comment, merge_pr) never touch
  task_dir/worktree, so no git setup is needed there.
"""

import os
import subprocess
import uuid
from pathlib import Path

import pytest

from orchestrator.scheduler import handle_agent_result, handle_agent_result_via_flow

# ---------------------------------------------------------------------------
# Paths to test fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
MOCK_AGENT = FIXTURES_DIR / "mock-agent.sh"
FAKE_GH_BIN = FIXTURES_DIR / "bin"


# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_gh_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prepend the fake gh binary directory to PATH for every test."""
    current_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{FAKE_GH_BIN}:{current_path}")


# ---------------------------------------------------------------------------
# Git repo helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> None:
    """Run a git command and raise on failure."""
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True)


def _init_git_repo_basic(path: Path) -> None:
    """Initialise a minimal git repo without a remote.

    The repo has one initial commit and its HEAD on the default branch.
    Used as TASK_WORKTREE for gatekeeper mock agents (they commit here
    but the scheduler never pushes from gatekeeper task dirs).
    """
    path.mkdir(parents=True, exist_ok=True)
    _git(["init"], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("init\n")
    _git(["add", "."], cwd=path)
    _git(["commit", "-m", "init"], cwd=path)


def _init_git_repo_with_remote(worktree: Path) -> None:
    """Initialise a git repo with a local bare remote.

    Used for implementer tests where the push_branch step must actually
    push commits to an 'origin'.  After setup the local repo has:
    - one initial commit on the default branch
    - a bare-repo origin with main branch
    - origin/HEAD pointing to main (so 'origin/HEAD..HEAD' ranges work)
    """
    worktree.mkdir(parents=True, exist_ok=True)

    # Set up bare remote first
    remote = worktree.parent / f"{worktree.name}.remote.git"
    remote.mkdir(parents=True, exist_ok=True)
    _git(["init", "--bare"], cwd=remote)

    # Local repo
    _git(["init"], cwd=worktree)
    _git(["config", "user.email", "test@example.com"], cwd=worktree)
    _git(["config", "user.name", "Test"], cwd=worktree)
    _git(["remote", "add", "origin", str(remote)], cwd=worktree)

    # Initial commit and push
    (worktree / "README.md").write_text("init\n")
    _git(["add", "."], cwd=worktree)
    _git(["commit", "-m", "init"], cwd=worktree)
    _git(["push", "origin", "HEAD:main"], cwd=worktree)

    # Make origin/HEAD resolve correctly so 'origin/HEAD..HEAD' works in
    # the submit_to_server step when counting commits.
    subprocess.run(
        ["git", "remote", "set-head", "origin", "main"],
        cwd=worktree, check=False, capture_output=True,
    )


# ---------------------------------------------------------------------------
# Mock agent helper
# ---------------------------------------------------------------------------


def _run_mock_agent(
    worktree: Path,
    task_dir: Path,
    *,
    commits: int = 1,
    outcome: str = "success",
    decision: str = "",
    comment: str = "",
    reason: str = "",
    crash: bool = False,
) -> subprocess.CompletedProcess:
    """Run mock-agent.sh with controlled environment variables.

    Args:
        worktree: Git repo the agent will cd into and commit from.
                  MUST be an initialised git repository.
        task_dir: Directory where result.json will be written.
        commits:  Number of mock git commits.  Note: on macOS, BSD ``seq``
                  counts down, so ``seq 1 N`` for N≤0 still iterates.
                  Always pass commits≥1 when you want predictable commits.
        outcome:  success | failure | needs_continuation (implementer mode).
        decision: approve | reject (gatekeeper mode; overrides outcome).
        comment:  Review comment for gatekeeper mode.
        reason:   Failure reason for implementer failure mode.
        crash:    If True, exits without writing result.json.
    """
    task_dir.mkdir(parents=True, exist_ok=True)

    env = {
        **os.environ,
        "TASK_WORKTREE": str(worktree),
        "TASK_DIR": str(task_dir),
        # Ensure fake gh is first on PATH even inside the subprocess
        "PATH": f"{FAKE_GH_BIN}:{os.environ.get('PATH', '')}",
        "MOCK_COMMITS": str(commits),
        "MOCK_OUTCOME": outcome,
        "MOCK_CRASH": "true" if crash else "false",
    }
    if decision:
        env["MOCK_DECISION"] = decision
    if comment:
        env["MOCK_COMMENT"] = comment
    if reason:
        env["MOCK_REASON"] = reason

    return subprocess.run(
        [str(MOCK_AGENT)],
        env=env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Task creation helpers
# ---------------------------------------------------------------------------


def _make_task_id() -> str:
    return f"MOCK-{uuid.uuid4().hex[:8].upper()}"


def _make_provisional(
    scoped_sdk,
    orchestrator_id: str,
    task_id: str | None = None,
) -> str:
    """Create and advance a task to the provisional queue.

    Returns the task ID.
    """
    if task_id is None:
        task_id = _make_task_id()

    scoped_sdk.tasks.create(
        id=task_id,
        file_path=f".octopoid/tasks/{task_id}.md",
        title=f"Mock provisional {task_id}",
        role="implement",
        branch="main",
    )
    scoped_sdk.tasks.claim(
        orchestrator_id=orchestrator_id,
        agent_name="mock-implementer",
        role_filter="implement",
    )
    scoped_sdk.tasks.submit(task_id, commits_count=1, turns_used=3)
    return task_id


# ---------------------------------------------------------------------------
# Pytest fixtures (for tests that want them in signatures)
# ---------------------------------------------------------------------------


@pytest.fixture
def test_repo(tmp_path: Path) -> Path:
    """Git repo with a local bare remote — usable by push_branch step."""
    worktree = tmp_path / "worktree"
    _init_git_repo_with_remote(worktree)
    return worktree


@pytest.fixture
def run_mock_agent():
    """Return the _run_mock_agent helper."""
    return _run_mock_agent


# ---------------------------------------------------------------------------
# Happy path — full lifecycle
# ---------------------------------------------------------------------------


class TestHappyPath:
    """Full lifecycle: implementer succeeds → gatekeeper approves → done."""

    def test_happy_path_lifecycle(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Full cycle: create → claim → mock implementer (2 commits) → provisional → mock gatekeeper approve → done."""
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Happy path lifecycle",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
        )

        # Implementer task dir: worktree needs a real git remote for push_branch
        impl_task_dir = tmp_path / "impl-task"
        impl_worktree = impl_task_dir / "worktree"
        _init_git_repo_with_remote(impl_worktree)

        # Run mock implementer (2 commits, success)
        result = _run_mock_agent(impl_worktree, impl_task_dir, commits=2, outcome="success")
        assert result.returncode == 0, f"Mock implementer failed: {result.stderr}"

        # Detach HEAD so push_branch can create the task-specific branch
        _git(["checkout", "--detach", "HEAD"], cwd=impl_worktree)

        # handle_agent_result → push_branch, run_tests, create_pr, submit_to_server
        handle_agent_result(task_id, "mock-implementer", impl_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "provisional", (
            f"Expected provisional after implementer success, got {task['queue']}"
        )

        # Gatekeeper: separate git repo for mock agent commits, plain dir for result.json
        gk_worktree = tmp_path / "gk-worktree"
        _init_git_repo_basic(gk_worktree)
        gk_task_dir = tmp_path / "gk-task"

        result = _run_mock_agent(
            gk_worktree, gk_task_dir,
            commits=1, decision="approve", comment="LGTM — looks good",
        )
        assert result.returncode == 0, f"Mock gatekeeper failed: {result.stderr}"

        # handle_agent_result_via_flow → post_review_comment, merge_pr → done
        handle_agent_result_via_flow(task_id, "mock-gatekeeper", gk_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "done", (
            f"Expected done after gatekeeper approval, got {task['queue']}"
        )


# ---------------------------------------------------------------------------
# Failure scenarios
# ---------------------------------------------------------------------------


class TestFailureScenarios:
    """Tests for failure and crash paths."""

    def test_agent_failure_goes_to_failed(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Mock agent returns outcome=failure → task moves to failed queue."""
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Failure scenario",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
        )

        worktree = tmp_path / "worktree"
        _init_git_repo_basic(worktree)
        task_dir = tmp_path / "task"

        result = _run_mock_agent(
            worktree, task_dir,
            commits=1, outcome="failure", reason="tests broke",
        )
        assert result.returncode == 0, f"Mock agent failed: {result.stderr}"

        handle_agent_result(task_id, "mock-implementer", task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "failed", (
            f"Expected failed queue, got {task['queue']}"
        )

    def test_agent_crash_goes_to_failed(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Mock agent crashes (no result.json) → task moves to failed (not stuck in claimed)."""
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Crash scenario",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
        )

        # Crash mode exits before cd, so worktree dir just needs to exist
        worktree = tmp_path / "worktree"
        worktree.mkdir(parents=True)
        task_dir = tmp_path / "task"
        task_dir.mkdir(parents=True)

        result = _run_mock_agent(worktree, task_dir, crash=True)
        assert result.returncode != 0, "Mock agent should exit non-zero in crash mode"
        assert not (task_dir / "result.json").exists()

        # No result.json → outcome=error → task moves to failed
        handle_agent_result(task_id, "mock-implementer", task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] != "claimed", "Task must not remain stuck in claimed after crash"
        assert task["queue"] == "failed", (
            f"Expected failed after crash, got {task['queue']}"
        )


# ---------------------------------------------------------------------------
# Gatekeeper flows
# ---------------------------------------------------------------------------


class TestGatekeeperFlows:
    """Tests for gatekeeper decision handling."""

    def test_gatekeeper_reject_returns_to_incoming(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Mock gatekeeper returns decision=reject → task goes back to incoming with feedback."""
        task_id = _make_provisional(scoped_sdk, orchestrator_id)

        gk_worktree = tmp_path / "gk-worktree"
        _init_git_repo_basic(gk_worktree)
        gk_task_dir = tmp_path / "gk-task"

        result = _run_mock_agent(
            gk_worktree, gk_task_dir,
            commits=1, decision="reject", comment="Needs more tests",
        )
        assert result.returncode == 0, f"Mock gatekeeper failed: {result.stderr}"

        handle_agent_result_via_flow(task_id, "mock-gatekeeper", gk_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "incoming", (
            f"Expected incoming after gatekeeper reject, got {task['queue']}"
        )

    def test_multiple_rejections_increment_count(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Rejecting 3 times: task returns to incoming each time; rejection_count increments if tracked."""
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Multiple rejections test",
            role="implement",
            branch="main",
        )

        for i in range(3):
            # Re-claim from incoming each round
            scoped_sdk.tasks.claim(
                orchestrator_id=orchestrator_id,
                agent_name="mock-implementer",
                role_filter="implement",
            )
            # Advance to provisional
            scoped_sdk.tasks.submit(task_id, commits_count=1, turns_used=3)

            # Mock gatekeeper rejects
            gk_worktree = tmp_path / f"gk-worktree-{i}"
            _init_git_repo_basic(gk_worktree)
            gk_task_dir = tmp_path / f"gk-task-{i}"

            result = _run_mock_agent(
                gk_worktree, gk_task_dir,
                commits=1, decision="reject", comment=f"Rejection #{i + 1}",
            )
            assert result.returncode == 0, f"Mock gatekeeper {i} failed: {result.stderr}"

            handle_agent_result_via_flow(task_id, "mock-gatekeeper", gk_task_dir)

            task = scoped_sdk.tasks.get(task_id)
            assert task is not None, f"Task not found after rejection {i + 1}"
            assert task["queue"] == "incoming", (
                f"Expected incoming after rejection {i + 1}, got {task['queue']}"
            )

        # After 3 rejections verify rejection_count if the server tracks it
        final_task = scoped_sdk.tasks.get(task_id)
        assert final_task is not None
        assert final_task["queue"] == "incoming"

        rejection_count = final_task.get("rejection_count") or final_task.get("rejections")
        if rejection_count is not None:
            assert rejection_count >= 3, (
                f"Expected rejection_count >= 3, got {rejection_count}"
            )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge-case behaviour."""

    def test_no_commits_success(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Mock agent succeeds with minimal commits → push_branch works, task reaches provisional.

        Note: BSD seq on macOS means MOCK_COMMITS=0 is not truly zero commits.
        This test uses commits=1 (the minimum) and verifies the full implementer
        flow path reaches provisional regardless of commit count.
        """
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Minimal commits test",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
        )

        impl_task_dir = tmp_path / "impl-task"
        impl_worktree = impl_task_dir / "worktree"
        _init_git_repo_with_remote(impl_worktree)

        # Use 1 commit — minimum that works reliably on all platforms
        result = _run_mock_agent(impl_worktree, impl_task_dir, commits=1, outcome="success")
        assert result.returncode == 0, f"Mock agent failed: {result.stderr}"

        # Detach HEAD so push_branch can create the task branch
        _git(["checkout", "--detach", "HEAD"], cwd=impl_worktree)

        handle_agent_result(task_id, "mock-implementer", impl_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "provisional", (
            f"Expected provisional after success, got {task['queue']}"
        )

    def test_needs_continuation(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Mock agent returns needs_continuation → task moves to needs_continuation queue."""
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Continuation test",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
        )

        worktree = tmp_path / "worktree"
        _init_git_repo_basic(worktree)
        task_dir = tmp_path / "task"

        result = _run_mock_agent(worktree, task_dir, commits=1, outcome="needs_continuation")
        assert result.returncode == 0, f"Mock agent failed: {result.stderr}"

        handle_agent_result(task_id, "mock-implementer", task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "needs_continuation", (
            f"Expected needs_continuation, got {task['queue']}"
        )


# ---------------------------------------------------------------------------
# Idempotent result handling (double-processing guard)
# ---------------------------------------------------------------------------


class TestIdempotentResultHandling:
    """Tests that handle_agent_result() is safe to call multiple times.

    If the scheduler processes the same result.json twice (e.g. race between
    PID cleanup and result handling), it must not cause duplicate transitions,
    errors, or data corruption.
    """

    def test_double_processing_is_idempotent(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Calling handle_agent_result() twice for the same result is a safe no-op.

        First call: task moves from claimed → provisional via flow steps.
        Second call: task already in provisional → guard triggers, no error,
        no double-submission, queue unchanged.
        """
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Idempotent result test",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
        )

        impl_task_dir = tmp_path / "impl-task"
        impl_worktree = impl_task_dir / "worktree"
        _init_git_repo_with_remote(impl_worktree)

        result = _run_mock_agent(impl_worktree, impl_task_dir, commits=1, outcome="success")
        assert result.returncode == 0, f"Mock agent failed: {result.stderr}"

        # Detach HEAD so push_branch can create the task-specific branch
        _git(["checkout", "--detach", "HEAD"], cwd=impl_worktree)

        # First call: task moves from claimed → provisional
        handle_agent_result(task_id, "mock-implementer", impl_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "provisional", (
            f"Expected provisional after first handle_agent_result, got {task['queue']}"
        )

        # Second call: same task_id and task_dir — must be a no-op, no exceptions
        handle_agent_result(task_id, "mock-implementer", impl_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "provisional", (
            f"Expected task to remain in provisional after double-processing, "
            f"got {task['queue']}"
        )

    def test_processing_result_for_done_task(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Calling handle_agent_result() on a task already in 'done' is a safe no-op.

        Simulates a late or duplicate result arriving after the full lifecycle
        has already completed. The queue guard in _handle_done_outcome must
        skip any transitions and leave the task in 'done'.
        """
        # Advance task to provisional via SDK helpers
        task_id = _make_provisional(scoped_sdk, orchestrator_id)

        # Advance provisional → done via gatekeeper approval
        gk_worktree = tmp_path / "gk-worktree"
        _init_git_repo_basic(gk_worktree)
        gk_task_dir = tmp_path / "gk-task"

        result = _run_mock_agent(
            gk_worktree, gk_task_dir,
            commits=1, decision="approve", comment="LGTM",
        )
        assert result.returncode == 0, f"Mock gatekeeper failed: {result.stderr}"

        handle_agent_result_via_flow(task_id, "mock-gatekeeper", gk_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "done", (
            f"Expected done after gatekeeper approval, got {task['queue']}"
        )

        # Simulate a late/duplicate implementer result arriving after task is done
        late_task_dir = tmp_path / "late-task"
        late_task_dir.mkdir(parents=True)
        (late_task_dir / "result.json").write_text('{"outcome": "done"}')

        # Must not raise, must not corrupt task state
        handle_agent_result(task_id, "mock-implementer", late_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "done", (
            f"Expected task to remain in done after late result processing, "
            f"got {task['queue']}"
        )


# ---------------------------------------------------------------------------
# merge_pr failure / success after gatekeeper approval
# ---------------------------------------------------------------------------


class TestMergePrFlows:
    """Tests for merge_pr step outcomes after gatekeeper approval.

    Requires TASK-test-4-1 (stateful fake gh) infrastructure.
    The fake gh binary supports GH_MOCK_MERGE_FAIL to simulate GitHub API
    errors during gh pr merge.
    """

    def test_merge_pr_failure_goes_to_failed(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When merge_pr raises (gh pr merge exits non-zero), task goes to failed."""
        # Advance task to provisional queue
        task_id = _make_provisional(scoped_sdk, orchestrator_id)

        # Set pr_number so hook_merge_pr actually calls gh pr merge (not SKIP)
        scoped_sdk.tasks.update(task_id, pr_number=int(os.environ.get("GH_MOCK_PR_NUMBER", "99")))

        # Tell fake gh to fail on gh pr merge
        monkeypatch.setenv("GH_MOCK_MERGE_FAIL", "true")

        # Run mock gatekeeper: decision=approve
        gk_worktree = tmp_path / "gk-worktree"
        _init_git_repo_basic(gk_worktree)
        gk_task_dir = tmp_path / "gk-task"

        result = _run_mock_agent(
            gk_worktree, gk_task_dir,
            commits=1, decision="approve", comment="LGTM",
        )
        assert result.returncode == 0, f"Mock gatekeeper failed: {result.stderr}"

        # Flow runs post_review_comment then merge_pr; merge_pr raises → failed
        handle_agent_result_via_flow(task_id, "mock-gatekeeper", gk_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "failed", (
            f"Expected failed after merge_pr error, got {task['queue']}"
        )

    def test_merge_pr_success_goes_to_done(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When merge_pr succeeds (gh pr merge exits 0), task goes to done."""
        # Advance task to provisional queue
        task_id = _make_provisional(scoped_sdk, orchestrator_id)

        # Set pr_number so hook_merge_pr actually calls gh pr merge (not SKIP)
        scoped_sdk.tasks.update(task_id, pr_number=int(os.environ.get("GH_MOCK_PR_NUMBER", "99")))

        # Ensure fake gh succeeds on gh pr merge (default behaviour)
        monkeypatch.delenv("GH_MOCK_MERGE_FAIL", raising=False)

        # Run mock gatekeeper: decision=approve
        gk_worktree = tmp_path / "gk-worktree"
        _init_git_repo_basic(gk_worktree)
        gk_task_dir = tmp_path / "gk-task"

        result = _run_mock_agent(
            gk_worktree, gk_task_dir,
            commits=1, decision="approve", comment="LGTM",
        )
        assert result.returncode == 0, f"Mock gatekeeper failed: {result.stderr}"

        # Flow runs post_review_comment then merge_pr; merge_pr succeeds → done
        handle_agent_result_via_flow(task_id, "mock-gatekeeper", gk_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "done", (
            f"Expected done after successful merge_pr, got {task['queue']}"
        )
