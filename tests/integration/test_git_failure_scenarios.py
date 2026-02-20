"""Git failure scenario integration tests using mock agents.

Tests error paths: merge conflicts, push failures, rebase instructions,
and create_pr step recovery.
Uses the same mock infrastructure as test_scheduler_mock.py.

No Claude API calls. No real GitHub API calls (uses fake gh CLI).

Run with a local server on port 9787:
    cd submodules/server && npx wrangler dev --port 9787
"""

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from orchestrator.scheduler import (
    AgentContext,
    guard_pr_mergeable,
    handle_agent_result,
    handle_agent_result_via_flow,
)
from orchestrator.state_utils import AgentState

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
# Git repo helpers (duplicated from test_scheduler_mock for module isolation)
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> None:
    """Run a git command and raise on failure."""
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True)


def _init_git_repo_basic(path: Path) -> None:
    """Initialise a minimal git repo without a remote."""
    path.mkdir(parents=True, exist_ok=True)
    _git(["init"], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("init\n")
    _git(["add", "."], cwd=path)
    _git(["commit", "-m", "init"], cwd=path)


def _init_git_repo_with_remote(worktree: Path) -> None:
    """Initialise a git repo with a local bare remote.

    After setup the local repo has one initial commit, a bare-repo origin
    with main branch, and origin/HEAD pointing to main.
    """
    worktree.mkdir(parents=True, exist_ok=True)

    remote = worktree.parent / f"{worktree.name}.remote.git"
    remote.mkdir(parents=True, exist_ok=True)
    _git(["init", "--bare"], cwd=remote)

    _git(["init"], cwd=worktree)
    _git(["config", "user.email", "test@example.com"], cwd=worktree)
    _git(["config", "user.name", "Test"], cwd=worktree)
    _git(["remote", "add", "origin", str(remote)], cwd=worktree)

    (worktree / "README.md").write_text("init\n")
    _git(["add", "."], cwd=worktree)
    _git(["commit", "-m", "init"], cwd=worktree)
    _git(["push", "origin", "HEAD:main"], cwd=worktree)

    subprocess.run(
        ["git", "remote", "set-head", "origin", "main"],
        cwd=worktree, check=False, capture_output=True,
    )


def _remote_path(worktree: Path) -> Path:
    """Return the bare remote path for a worktree created by _init_git_repo_with_remote."""
    return worktree.parent / f"{worktree.name}.remote.git"


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
    """Run mock-agent.sh with controlled environment variables."""
    task_dir.mkdir(parents=True, exist_ok=True)

    env = {
        **os.environ,
        "TASK_WORKTREE": str(worktree),
        "TASK_DIR": str(task_dir),
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
    return f"GITF-{uuid.uuid4().hex[:8].upper()}"


def _make_provisional(
    scoped_sdk,
    orchestrator_id: str,
    task_id: str | None = None,
) -> str:
    """Create and advance a task to the provisional queue. Returns the task ID."""
    if task_id is None:
        task_id = _make_task_id()

    scoped_sdk.tasks.create(
        id=task_id,
        file_path=f".octopoid/tasks/{task_id}.md",
        title=f"Git failure test {task_id}",
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


def _make_agent_ctx(task: dict, tmp_path: Path) -> AgentContext:
    """Build a minimal AgentContext with the given task as claimed_task."""
    return AgentContext(
        agent_config={},
        agent_name="mock-gatekeeper",
        role="review",
        interval=300,
        state=AgentState(),
        state_path=tmp_path / "state.json",
        claimed_task=task,
    )


# ---------------------------------------------------------------------------
# Merge conflict scenarios
# ---------------------------------------------------------------------------


class TestMergeConflictScenarios:
    """Tests for PR merge conflict detection and handling."""

    def test_pr_merge_conflict_blocks_acceptance(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Task in provisional with CONFLICTING PR → guard_pr_mergeable rejects back to incoming.

        Simulates the guard chain reaching guard_pr_mergeable after claim_task
        has already moved the task to claimed.  The guard detects the conflict
        via the fake gh CLI and calls sdk.tasks.reject, which moves the task
        back to incoming.
        """
        task_id = _make_provisional(scoped_sdk, orchestrator_id)

        # Give the task a PR number so guard_pr_mergeable actually checks it
        scoped_sdk.tasks.update(task_id, pr_number=99)

        # Fake gh returns CONFLICTING for all pr view calls
        monkeypatch.setenv("GH_MOCK_MERGE_STATUS", "CONFLICTING")

        # Fetch the updated task and simulate the guard having claimed it
        task = scoped_sdk.tasks.get(task_id)
        ctx = _make_agent_ctx(task, tmp_path)

        # Call the guard directly (mirrors what evaluate_agent does)
        should_proceed, reason = guard_pr_mergeable(ctx)

        assert not should_proceed, (
            "guard_pr_mergeable should block when PR is CONFLICTING"
        )
        assert "pr_conflicts" in reason or "rebase" in reason.lower(), (
            f"Expected conflict reason, got: {reason!r}"
        )

        # Task must be back in incoming so the implementer can rebase
        final_task = scoped_sdk.tasks.get(task_id)
        assert final_task is not None
        assert final_task["queue"] == "incoming", (
            f"Expected incoming after conflict guard, got {final_task['queue']}"
        )

    def test_merge_fails_at_merge_time(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Gatekeeper approves but gh pr merge fails → task moves to failed, not done.

        The merge_pr step calls approve_and_merge which runs BEFORE_MERGE hooks.
        The hook_merge_pr hook calls 'gh pr merge'.  With GH_MOCK_MERGE_FAIL=true
        the fake gh exits non-zero, causing the hook to fail.  approve_and_merge
        returns an error dict, merge_pr raises RuntimeError, and
        handle_agent_result_via_flow's outer except handler moves the task to failed.
        """
        task_id = _make_provisional(scoped_sdk, orchestrator_id)

        # Set pr_number so the merge hook has something to merge
        scoped_sdk.tasks.update(task_id, pr_number=99)

        # Make gh pr merge always fail
        monkeypatch.setenv("GH_MOCK_MERGE_FAIL", "true")

        gk_worktree = tmp_path / "gk-worktree"
        _init_git_repo_basic(gk_worktree)
        gk_task_dir = tmp_path / "gk-task"

        result = _run_mock_agent(
            gk_worktree, gk_task_dir,
            commits=1, decision="approve", comment="LGTM",
        )
        assert result.returncode == 0, f"Mock gatekeeper failed: {result.stderr}"

        # The merge step will raise; the outer except must catch it
        handle_agent_result_via_flow(task_id, "mock-gatekeeper", gk_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] != "done", (
            "Task must NOT reach done when gh pr merge fails"
        )
        assert task["queue"] == "failed", (
            f"Expected failed after merge error, got {task['queue']}"
        )


# ---------------------------------------------------------------------------
# Push failure scenarios
# ---------------------------------------------------------------------------


class TestPushFailureScenarios:
    """Tests for git push failure handling."""

    def test_push_branch_failure(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Push fails because remote is deleted → task stays in claimed (not orphaned).

        When push_branch raises CalledProcessError, handle_agent_result catches it
        and leaves the task in claimed so the lease monitor can recover it later.
        The task must never move to provisional (which would indicate a false success).
        """
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Push failure test",
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

        # Delete the remote so git push has nowhere to go
        remote = _remote_path(impl_worktree)
        shutil.rmtree(remote)

        # handle_agent_result tries push_branch → CalledProcessError → re-raised
        # on first attempt (retry mechanism: attempt 1/3). The task stays in
        # claimed because the step failure prevents the transition.
        with pytest.raises(subprocess.CalledProcessError):
            handle_agent_result(task_id, "mock-implementer", impl_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] != "provisional", (
            "Task must not advance to provisional when push fails"
        )
        assert task["queue"] == "claimed", (
            f"Expected task to remain in claimed after push failure, got {task['queue']}"
        )

    def test_push_branch_no_diff(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Push succeeds even when branch is already up-to-date with remote.

        Simulates an agent that already pushed its commits (e.g. from a previous
        partial run).  'git push -u origin <branch>' exits 0 with 'Everything
        up-to-date', and the task still reaches provisional.
        """
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Push no diff test",
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

        # Agent makes commits (BSD seq means at least 1 even if 0 requested)
        result = _run_mock_agent(impl_worktree, impl_task_dir, commits=1, outcome="success")
        assert result.returncode == 0, f"Mock agent failed: {result.stderr}"

        # Pre-push the task branch so origin is already up-to-date
        task_branch = f"agent/{task_id}"
        _git(["checkout", "-b", task_branch], cwd=impl_worktree)
        _git(["push", "origin", task_branch], cwd=impl_worktree)
        # Stay on the named branch (not detached HEAD) — ensure_on_branch will
        # see we're already on the right branch and skip creation

        # handle_agent_result → push_branch → "Everything up-to-date" (exit 0)
        handle_agent_result(task_id, "mock-implementer", impl_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "provisional", (
            f"Expected provisional after no-diff push, got {task['queue']}"
        )


# ---------------------------------------------------------------------------
# Rebase instructions
# ---------------------------------------------------------------------------


class TestRebaseInstructions:
    """Tests that rejection feedback includes actionable rebase instructions."""

    def test_rejected_task_gets_rebase_instructions(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Gatekeeper rejection appends rebase instructions referencing the correct base branch.

        reject_with_feedback builds the rejection reason by appending a rebase
        block when 'git rebase' is not already in the comment.  The rebase block
        names the branch returned by get_base_branch() so the implementer knows
        exactly what to rebase onto.

        Note: handle_agent_result_via_flow only calls reject_with_feedback when
        status=="success" and decision=="reject".  When status=="failure" it takes
        a different (shorter) path.  We write result.json directly to produce the
        correct {"status": "success", "decision": "reject"} payload.
        """
        import json

        from orchestrator import queue_utils
        from orchestrator.config import get_base_branch

        task_id = _make_provisional(scoped_sdk, orchestrator_id)

        # Write result.json directly with status="success" so the flow takes the
        # decision=="reject" branch, which calls reject_with_feedback.
        gk_task_dir = tmp_path / "gk-task"
        gk_task_dir.mkdir(parents=True, exist_ok=True)
        gk_result = {
            "status": "success",
            "decision": "reject",
            # Comment does NOT include "git rebase" — reject_with_feedback must add it
            "comment": "Needs more tests",
        }
        (gk_task_dir / "result.json").write_text(json.dumps(gk_result))

        # Capture the reason passed to sdk.tasks.reject so we can inspect it
        captured_reasons: list[str] = []

        real_get_sdk = queue_utils.get_sdk

        def capturing_get_sdk():
            real_sdk = real_get_sdk()
            original_reject = real_sdk.tasks.reject

            def wrapped_reject(tid: str, reason: str, **kwargs):
                captured_reasons.append(reason)
                return original_reject(tid, reason, **kwargs)

            real_sdk.tasks.reject = wrapped_reject
            return real_sdk

        monkeypatch.setattr(queue_utils, "get_sdk", capturing_get_sdk)

        handle_agent_result_via_flow(task_id, "mock-gatekeeper", gk_task_dir)

        # Task must be back in incoming
        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "incoming", (
            f"Expected incoming after gatekeeper reject, got {task['queue']}"
        )

        # Rejection reason must include rebase instructions with the correct base branch
        assert captured_reasons, "sdk.tasks.reject must have been called"
        reason = captured_reasons[0]
        base_branch = get_base_branch()

        assert "git rebase" in reason, (
            f"Rejection reason should include 'git rebase'; got: {reason!r}"
        )
        assert base_branch in reason, (
            f"Rejection reason should reference base branch '{base_branch}'; got: {reason!r}"
        )


# ---------------------------------------------------------------------------
# Combined scenarios
# ---------------------------------------------------------------------------


class TestCombinedScenarios:
    """Tests for chained error paths (reject → re-claim → still conflicting)."""

    def test_conflict_after_rejection(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Task rejected → re-claimed → PR still CONFLICTING → guard rejects again.

        Verifies the cycle works correctly without the task getting stuck in any
        intermediate state:

            incoming → (reject) → incoming → (guard conflict) → incoming

        The task must end in incoming each time, never stuck in provisional
        or claimed.
        """
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Conflict after rejection test",
            role="implement",
            branch="main",
        )

        # --- Round 1: implementer submits, gatekeeper rejects ---
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
        )
        scoped_sdk.tasks.submit(task_id, commits_count=1, turns_used=3)

        gk_worktree1 = tmp_path / "gk-worktree-1"
        _init_git_repo_basic(gk_worktree1)
        gk_task_dir1 = tmp_path / "gk-task-1"

        result = _run_mock_agent(
            gk_worktree1, gk_task_dir1,
            commits=1, decision="reject", comment="Not ready",
        )
        assert result.returncode == 0, f"First gatekeeper failed: {result.stderr}"

        handle_agent_result_via_flow(task_id, "mock-gatekeeper", gk_task_dir1)

        task_after_reject = scoped_sdk.tasks.get(task_id)
        assert task_after_reject is not None
        assert task_after_reject["queue"] == "incoming", (
            f"Expected incoming after first rejection, got {task_after_reject['queue']}"
        )

        # --- Round 2: re-claim and re-submit to provisional (simulating re-attempt) ---
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="mock-implementer",
            role_filter="implement",
        )
        scoped_sdk.tasks.submit(task_id, commits_count=1, turns_used=3)

        # Attach a PR number so guard_pr_mergeable actually fires
        scoped_sdk.tasks.update(task_id, pr_number=99)

        # PR is still CONFLICTING after the re-submit
        monkeypatch.setenv("GH_MOCK_MERGE_STATUS", "CONFLICTING")

        # Simulate guard_claim_task claiming the task (moving it to claimed state)
        # then guard_pr_mergeable detecting the conflict.
        task = scoped_sdk.tasks.get(task_id)
        ctx = _make_agent_ctx(task, tmp_path)

        should_proceed, reason = guard_pr_mergeable(ctx)

        assert not should_proceed, (
            "guard_pr_mergeable should block on CONFLICTING even after a rejection cycle"
        )

        final_task = scoped_sdk.tasks.get(task_id)
        assert final_task is not None
        assert final_task["queue"] == "incoming", (
            f"Task must cycle back to incoming after second conflict guard, "
            f"got {final_task['queue']}"
        )


# ---------------------------------------------------------------------------
# create_pr step failure recovery
# ---------------------------------------------------------------------------


class TestCreatePrFailureRecovery:
    """Tests for create_pr step failure and recovery scenarios."""

    def test_pr_already_exists_step_recovers(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """create_pr step recovers when a PR already exists for the task branch.

        Simulates the scenario where a PR was created by a previous partial run.
        The step calls gh pr view first — the stateful fake gh returns the
        pre-existing PR from the state file — so create_pr returns that PR
        without calling gh pr create.

        The task must advance to provisional, and pr_url must be set.
        """
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="PR exists recovery test",
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

        result = _run_mock_agent(impl_worktree, impl_task_dir, commits=1, outcome="done")
        assert result.returncode == 0, f"Mock agent failed: {result.stderr}"

        # Detach HEAD so push_branch step can create the task-specific branch
        _git(["checkout", "--detach", "HEAD"], cwd=impl_worktree)

        # Pre-create a PR in the stateful fake gh state for the expected branch
        task_branch = f"agent/{task_id}"
        pr_url = "https://github.com/mock/repo/pull/42"
        state_file = tmp_path / "gh_state.json"
        state_file.write_text(json.dumps({
            "prs": {
                task_branch: {"url": pr_url, "number": 42},
            }
        }))
        monkeypatch.setenv("GH_STATE_FILE", str(state_file))

        handle_agent_result(task_id, "mock-implementer", impl_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "provisional", (
            f"Expected provisional after create_pr recovery, got {task['queue']}"
        )
        assert task.get("pr_url") == pr_url, (
            f"Expected pr_url={pr_url!r}, got {task.get('pr_url')!r}"
        )

    def test_pr_create_fails_unknown_error(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """create_pr step leaves task in claimed when gh pr create fails with unknown error.

        GH_MOCK_CREATE_FAIL=true makes gh pr create exit non-zero with a generic
        error that does NOT contain "already exists". The create_pr step raises
        CalledProcessError, which handle_agent_result's outer except handler
        catches and swallows — leaving the task in claimed for lease recovery.
        """
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="PR create fail test",
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

        result = _run_mock_agent(impl_worktree, impl_task_dir, commits=1, outcome="done")
        assert result.returncode == 0, f"Mock agent failed: {result.stderr}"

        # Detach HEAD so push_branch step can create the task-specific branch
        _git(["checkout", "--detach", "HEAD"], cwd=impl_worktree)

        # Force gh pr create to fail with a generic (non-"already exists") error
        monkeypatch.setenv("GH_MOCK_CREATE_FAIL", "true")

        # handle_agent_result re-raises on first attempt (retry mechanism: 1/3).
        # The task stays in claimed because the step failure prevents the transition.
        with pytest.raises(subprocess.CalledProcessError):
            handle_agent_result(task_id, "mock-implementer", impl_task_dir)

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "claimed", (
            f"Expected task to remain in claimed after create_pr failure, "
            f"got {task['queue']}"
        )
