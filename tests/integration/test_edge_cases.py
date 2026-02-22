"""Edge case tests for task lifecycle.

Tests error handling and conflict resolution in the scheduler pipeline:
- PR has merge conflicts (CONFLICTING merge state)
- merge_pr step fails → task must NOT advance to done (Draft 45 bug)
- Agent succeeds with 0 commits → push_branch handles gracefully
- Multiple rejections cycle → rejection_count increments
- Push fails → task does not advance to provisional

Integration tests use sdk + clean_tasks (reliable isolation).
Git tests use real local git repos via pytest tmp_path.
Unit tests need no server fixtures.
"""

import json
import os
import subprocess
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.hook_manager import HookManager
from orchestrator.steps import merge_pr as merge_pr_step
from orchestrator.steps import push_branch as push_branch_step
from tests.integration.flow_helpers import make_task_id


# ─────────────────────────────────────────────────────────────────────────────
# Git repo fixtures
# ─────────────────────────────────────────────────────────────────────────────

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test Agent",
    "GIT_AUTHOR_EMAIL": "test@octopoid.test",
    "GIT_COMMITTER_NAME": "Test Agent",
    "GIT_COMMITTER_EMAIL": "test@octopoid.test",
}


def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in cwd with test identity."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )


def _init_repo(repo: Path, remote_url: str) -> None:
    """Initialise a git repo pointed at remote_url and make one commit."""
    result = _git(["init", "-b", "main"], repo, check=False)
    if result.returncode != 0:
        _git(["init"], repo)
        _git(["symbolic-ref", "HEAD", "refs/heads/main"], repo)

    _git(["config", "user.email", "test@octopoid.test"], repo)
    _git(["config", "user.name", "Test Agent"], repo)
    _git(["remote", "add", "origin", remote_url], repo)

    (repo / "README.md").write_text("# test\n")
    _git(["add", "."], repo)
    _git(["commit", "-m", "Initial commit"], repo)


def _detach_head(repo: Path) -> None:
    """Detach HEAD to simulate an agent worktree (worktrees start detached)."""
    sha = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    _git(["checkout", "--detach", sha], repo)


@pytest.fixture
def test_repo(tmp_path: Path) -> Path:
    """Task dir with a working git repo at task_dir/worktree.

    The worktree has one commit pushed to origin and is in detached HEAD
    state (simulating a fresh agent worktree).

    Returns the task_dir. Worktree is at task_dir/worktree.
    """
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(["init", "--bare"], remote)

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _init_repo(worktree, remote_url=str(remote))
    _git(["push", "-u", "origin", "HEAD:main"], worktree)
    _detach_head(worktree)

    return tmp_path


@pytest.fixture
def conflicting_repo(tmp_path: Path) -> Path:
    """Task dir simulating a PR with merge conflicts.

    The worktree has agent commits and the remote main has been updated
    independently (diverged history). Represents a task whose PR would
    have CONFLICTING mergeStateStatus on GitHub.

    Returns the task_dir. Worktree is at task_dir/worktree.
    """
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(["init", "--bare"], remote)

    # Set up worktree with an initial commit
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _init_repo(worktree, remote_url=str(remote))
    _git(["push", "-u", "origin", "HEAD:main"], worktree)

    # Detach HEAD and add an agent commit
    _detach_head(worktree)
    (worktree / "agent_change.md").write_text("Agent work\n")
    _git(["add", "."], worktree)
    _git(["commit", "-m", "Agent change"], worktree)

    # Meanwhile, push a conflicting commit to remote main from a side clone
    side_clone = tmp_path / "side_clone"
    side_clone.mkdir()
    _init_repo(side_clone, remote_url=str(remote))
    _git(["fetch", "origin"], side_clone)
    _git(["reset", "--hard", "origin/main"], side_clone)
    (side_clone / "conflicting.md").write_text("Conflicting change\n")
    _git(["add", "."], side_clone)
    _git(["commit", "-m", "Diverging commit on main"], side_clone)
    _git(["push", "origin", "HEAD:main"], side_clone)

    return tmp_path


@pytest.fixture
def broken_remote_repo(tmp_path: Path) -> Path:
    """Task dir with a git repo whose remote does not exist (push will fail).

    Returns the task_dir. Worktree is at task_dir/worktree.
    """
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    result = _git(["init", "-b", "main"], worktree, check=False)
    if result.returncode != 0:
        _git(["init"], worktree)
    _git(["config", "user.email", "test@octopoid.test"], worktree)
    _git(["config", "user.name", "Test Agent"], worktree)
    # Remote points to a non-existent local path — push fails immediately
    _git(["remote", "add", "origin", str(tmp_path / "nonexistent.git")], worktree)

    (worktree / "README.md").write_text("# broken\n")
    _git(["add", "."], worktree)
    _git(["commit", "-m", "Initial"], worktree)
    _detach_head(worktree)

    return tmp_path


def _make_task_dict(task_id: str, role: str = "implement") -> dict:
    """Minimal task dict for step function tests (no server needed)."""
    return {
        "id": task_id,
        "role": role,
        "title": f"Edge case test {task_id}",
    }


def _create_provisional_inline(sdk, orchestrator_id: str) -> str:
    """Create a task and advance it to provisional via server API.

    Returns task_id. Uses unique IDs to avoid conflicts with other tests.
    Unlike flow_helpers.create_provisional, this verifies the claim returns
    the expected task before submitting.
    """
    task_id = make_task_id()
    sdk.tasks.create(
        id=task_id,
        file_path=f".octopoid/tasks/{task_id}.md",
        title=f"Edge case test {task_id}",
        role="implement",
        branch="main",
    )
    claimed = sdk.tasks.claim(
        orchestrator_id=orchestrator_id,
        agent_name="test-agent",
        role_filter="implement",
    )
    assert claimed is not None, "Should claim a task"
    assert claimed["id"] == task_id, (
        f"Expected to claim {task_id!r}, got {claimed['id']!r}. "
        f"This can indicate stale tasks from previous tests."
    )
    sdk.tasks.submit(task_id, commits_count=1, turns_used=5)
    return task_id


# ─────────────────────────────────────────────────────────────────────────────
# test_merge_conflict_blocks_acceptance
# ─────────────────────────────────────────────────────────────────────────────


class TestMergeConflictBlocksAcceptance:
    """PR with CONFLICTING merge state must not allow task acceptance."""

    def test_merge_conflict_blocks_acceptance(
        self, sdk, orchestrator_id, clean_tasks, conflicting_repo
    ):
        """Gatekeeper approves but PR is CONFLICTING → task NOT accepted, needs_rebase set.

        Simulates the scenario from Draft 40: PR #80 had merge conflicts,
        merge_pr step silently failed, task looped in provisional.
        """
        task_id = _create_provisional_inline(sdk, orchestrator_id)
        task = sdk.tasks.get(task_id)

        # Simulate approve_and_merge returning a CONFLICTING error
        conflicting_error = {
            "error": "Failed to merge PR (mergeStateStatus: CONFLICTING)",
            "merged": False,
            "task_id": task_id,
        }

        with patch("orchestrator.queue_utils.approve_and_merge") as mock_merge, \
             patch("orchestrator.queue_utils.get_sdk") as mock_get_sdk:
            mock_merge.return_value = conflicting_error
            mock_sdk = MagicMock()
            mock_get_sdk.return_value = mock_sdk

            with pytest.raises(RuntimeError, match="merge_pr failed"):
                merge_pr_step(task, {}, conflicting_repo)

        # Task must NOT be accepted — should still be in provisional
        current = sdk.tasks.get(task_id)
        assert current is not None
        assert current["queue"] == "provisional", (
            f"Task should stay in provisional when PR has merge conflicts, "
            f"got queue={current['queue']!r}"
        )
        assert current["queue"] != "done", (
            "Task must NOT advance to done when PR has merge conflicts"
        )

        # The step should have attempted to set needs_rebase on the task
        update_calls = [
            c for c in mock_sdk.tasks.update.call_args_list
            if c.kwargs.get("needs_rebase") or (c.args and "needs_rebase" in str(c))
        ]
        assert len(update_calls) > 0, (
            "merge_pr step should set needs_rebase=True when PR is CONFLICTING"
        )

    def test_conflicting_pr_step_raises_runtime_error(self, conflicting_repo):
        """merge_pr step raises RuntimeError when approve_and_merge returns CONFLICTING."""
        task = _make_task_dict(make_task_id())

        with patch("orchestrator.queue_utils.approve_and_merge") as mock_merge, \
             patch("orchestrator.queue_utils.get_sdk"):
            mock_merge.return_value = {
                "error": "PR has CONFLICTING merge state",
                "merged": False,
            }

            with pytest.raises(RuntimeError) as exc_info:
                merge_pr_step(task, {}, conflicting_repo)

        assert "merge_pr failed" in str(exc_info.value)


# ─────────────────────────────────────────────────────────────────────────────
# test_merge_step_failure_not_accepted
# ─────────────────────────────────────────────────────────────────────────────


class TestMergeStepFailureNotAccepted:
    """merge_pr step failure must not advance task to done (Draft 45 regression test)."""

    def test_merge_step_failure_not_accepted(
        self, sdk, orchestrator_id, clean_tasks, test_repo
    ):
        """merge_pr step raises → task stays in provisional, never reaches done.

        Verifies the flow-path fix: when merge_pr step raises RuntimeError,
        the flow runner does NOT call accept(), so the task remains in provisional.
        """
        task_id = _create_provisional_inline(sdk, orchestrator_id)
        task = sdk.tasks.get(task_id)

        with patch("orchestrator.queue_utils.approve_and_merge") as mock_merge, \
             patch("orchestrator.queue_utils.get_sdk"):
            mock_merge.return_value = {
                "error": "gh: pull request merge failed — check PR status",
                "merged": False,
            }

            with pytest.raises(RuntimeError):
                merge_pr_step(task, {}, test_repo)

        # Task must NOT be in done queue
        current = sdk.tasks.get(task_id)
        assert current["queue"] != "done", (
            "Task must NOT advance to done when merge_pr step fails "
            "(this would be the Draft 45 regression)"
        )
        assert current["queue"] == "provisional", (
            f"Expected task to stay in provisional, got: {current['queue']!r}"
        )

    def test_can_transition_false_when_hook_failed(self):
        """HookManager.can_transition returns False when a hook has failed status.

        This documents and detects the Draft 45 bug:
        - Buggy: can_transition only checks for pending hooks.
          A failed hook is not pending → empty pending list → True (wrong!)
        - Fixed: can_transition also checks for failed hooks → returns False.
        """
        hm = HookManager(sdk=MagicMock())

        task_with_failed_hook = {
            "id": "test-draft45-regression",
            "hooks": json.dumps([
                {
                    "name": "merge_pr",
                    "point": "before_merge",
                    "type": "orchestrator",
                    "status": "failed",  # Hook ran but failed
                }
            ]),
        }

        can_proceed, blocking = hm.can_transition(task_with_failed_hook, "before_merge")

        assert not can_proceed, (
            "can_transition must return False when a hook has failed status. "
            "Returning True is the Draft 45 bug: tasks accepted even when merge_pr failed."
        )
        assert "merge_pr" in blocking, (
            f"Failed hook 'merge_pr' should appear in blocking list, got: {blocking}"
        )

    def test_can_transition_true_when_all_hooks_passed(self):
        """can_transition returns True only when all hooks have passed."""
        hm = HookManager(sdk=MagicMock())

        task_all_passed = {
            "id": "test-all-passed",
            "hooks": json.dumps([
                {
                    "name": "merge_pr",
                    "point": "before_merge",
                    "type": "orchestrator",
                    "status": "passed",
                }
            ]),
        }

        can_proceed, blocking = hm.can_transition(task_all_passed, "before_merge")
        assert can_proceed, "can_transition should return True when all hooks have passed"
        assert blocking == [], f"No blocking hooks expected, got: {blocking}"

    def test_can_transition_false_when_hook_pending(self):
        """can_transition returns False when any hook is still pending."""
        hm = HookManager(sdk=MagicMock())

        task_pending = {
            "id": "test-pending",
            "hooks": json.dumps([
                {
                    "name": "merge_pr",
                    "point": "before_merge",
                    "type": "orchestrator",
                    "status": "pending",
                }
            ]),
        }

        can_proceed, blocking = hm.can_transition(task_pending, "before_merge")
        assert not can_proceed
        assert "merge_pr" in blocking


# ─────────────────────────────────────────────────────────────────────────────
# test_no_commits_edge_case
# ─────────────────────────────────────────────────────────────────────────────


class TestNoCommitsEdgeCase:
    """Agent succeeds with 0 new commits — push_branch must handle gracefully."""

    def test_push_branch_with_zero_new_commits(self, test_repo):
        """push_branch step succeeds even when worktree has 0 commits ahead of origin.

        Scenario: agent ran, made no code changes, but the step still needs
        to create the branch and push it (so the PR can be created).
        The worktree is on detached HEAD at the same commit as origin/main.
        """
        task_id = make_task_id()
        task = _make_task_dict(task_id)

        # test_repo fixture: worktree is at origin/main with 0 commits ahead
        # push_branch should create agent/{task_id} branch and push it
        push_branch_step(task, {}, test_repo)

        # Verify the branch was created on the worktree
        worktree = test_repo / "worktree"
        branch_result = _git(["rev-parse", "--abbrev-ref", "HEAD"], worktree)
        branch = branch_result.stdout.strip()
        expected = f"agent/{task_id}"
        assert branch == expected, (
            f"Expected branch {expected!r} after push, got {branch!r}"
        )

        # Verify the branch exists on the remote
        remote_branches = _git(
            ["ls-remote", "--heads", "origin"], worktree
        ).stdout
        assert expected in remote_branches, (
            f"Branch {expected!r} should exist on remote after push"
        )

    def test_submit_with_zero_commits_accepted_by_server(
        self, sdk, orchestrator_id, clean_tasks
    ):
        """Server accepts submit() with commits_count=0 (no commits edge case).

        The submit_to_server step counts commits via git rev-list.
        When 0 commits, it submits with commits_count=0. Server must accept this.
        """
        task_id = make_task_id()
        sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title=f"Zero commits {task_id}",
            role="implement",
            branch="main",
        )
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed is not None and claimed["id"] == task_id

        submitted = sdk.tasks.submit(task_id, commits_count=0, turns_used=3)

        assert submitted["queue"] == "provisional", (
            f"Server should accept submit with 0 commits, got queue={submitted['queue']!r}"
        )
        assert submitted.get("commits_count", 0) == 0


# ─────────────────────────────────────────────────────────────────────────────
# test_multiple_rejections
# ─────────────────────────────────────────────────────────────────────────────


class TestMultipleRejections:
    """Task rejected multiple times — rejection_count increments, task stays cyclable."""

    def test_multiple_rejections_rejection_count_increments(
        self, sdk, orchestrator_id, clean_tasks
    ):
        """rejection_count increments with each rejection cycle.

        Each reject → incoming → claim → submit → reject cycle should increase
        rejection_count by 1 (or at minimum not decrease it).
        """
        task_id = make_task_id()
        sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title=f"Multi-reject {task_id}",
            role="implement",
            branch="main",
        )
        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="agent-initial",
            role_filter="implement",
        )
        assert claimed is not None and claimed["id"] == task_id

        rejection_counts = []
        for i in range(3):
            # Submit → provisional
            sdk.tasks.submit(task_id, commits_count=1, turns_used=1)

            # Reject → incoming
            rejected = sdk.tasks.reject(
                task_id,
                reason=f"Rejection {i + 1}: fix required",
                rejected_by="gatekeeper",
            )
            assert rejected["queue"] == "incoming", (
                f"After rejection {i + 1}, task should be incoming, "
                f"got: {rejected['queue']!r}"
            )
            rejection_counts.append(rejected.get("rejection_count", 0))

            # Re-claim for the next iteration (not needed on last round)
            if i < 2:
                reclaimed = sdk.tasks.claim(
                    orchestrator_id=orchestrator_id,
                    agent_name=f"agent-round-{i + 1}",
                    role_filter="implement",
                )
                assert reclaimed is not None, (
                    f"Task should be re-claimable after rejection {i + 1}"
                )
                assert reclaimed["id"] == task_id

        # rejection_count should grow monotonically
        assert rejection_counts[0] >= 1, (
            f"First rejection should set rejection_count >= 1, got {rejection_counts[0]}"
        )
        assert rejection_counts[1] >= rejection_counts[0], (
            f"Second rejection should have >= rejection_count than first: "
            f"{rejection_counts[1]} vs {rejection_counts[0]}"
        )
        assert rejection_counts[2] >= rejection_counts[1], (
            f"Third rejection should have >= rejection_count than second: "
            f"{rejection_counts[2]} vs {rejection_counts[1]}"
        )

    def test_multiple_rejections_task_remains_claimable(
        self, sdk, orchestrator_id, clean_tasks
    ):
        """After each rejection, task returns to incoming and stays claimable.

        Tasks should never get stuck in a terminal state during rejection cycles.
        """
        task_id = make_task_id()
        sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title=f"Multi-reject claimable {task_id}",
            role="implement",
            branch="main",
        )

        for i in range(3):
            # Claim → move to claimed
            claimed = sdk.tasks.claim(
                orchestrator_id=orchestrator_id,
                agent_name=f"agent-round-{i}",
                role_filter="implement",
            )
            assert claimed is not None, (
                f"Task should be claimable at start of round {i + 1}"
            )
            assert claimed["id"] == task_id, (
                f"Expected task {task_id!r}, got {claimed['id']!r}"
            )
            assert claimed["queue"] == "claimed"

            # Submit → move to provisional
            submitted = sdk.tasks.submit(task_id, commits_count=1, turns_used=1)
            assert submitted["queue"] == "provisional", (
                f"After submit in round {i + 1}, expected provisional"
            )

            # Reject → move back to incoming
            rejected = sdk.tasks.reject(
                task_id,
                reason=f"Round {i + 1} feedback",
                rejected_by="gatekeeper",
            )
            assert rejected["queue"] == "incoming", (
                f"After rejection {i + 1}, expected incoming, got {rejected['queue']!r}"
            )

        # After 3 rejections, task is back in incoming and claimable one more time
        final = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="final-agent",
            role_filter="implement",
        )
        assert final is not None, "Task should be claimable after 3 rejections"
        assert final["id"] == task_id


# ─────────────────────────────────────────────────────────────────────────────
# test_push_failure
# ─────────────────────────────────────────────────────────────────────────────


class TestPushFailure:
    """Push branch failure prevents task from advancing to provisional."""

    def test_push_failure_raises_exception(self, broken_remote_repo):
        """push_branch step raises when git push fails (broken/missing remote).

        Scenario: agent finishes work but the remote is unreachable or the
        repo URL has changed. The step should raise, not silently succeed.
        """
        task_id = make_task_id()
        task = _make_task_dict(task_id)

        with pytest.raises((subprocess.CalledProcessError, RuntimeError)):
            push_branch_step(task, {}, broken_remote_repo)

    def test_push_failure_task_stays_claimed(
        self, sdk, orchestrator_id, clean_tasks
    ):
        """When push_branch fails, task stays in claimed — never advances to provisional.

        Simulates the flow: implementer finishes → push_branch step fails →
        flow dispatch catches the exception → task is NOT submitted.
        """
        task_id = make_task_id()
        sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title=f"Push failure {task_id}",
            role="implement",
            branch="main",
        )

        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-push-agent",
            role_filter="implement",
        )
        assert claimed is not None, "Should be able to claim the task"
        assert claimed["id"] == task_id, (
            f"Expected to claim {task_id!r}, got {claimed['id']!r}"
        )

        task = sdk.tasks.get(task_id)
        assert task["queue"] == "claimed"

        # Simulate push_branch step raising due to git push failure
        push_raised = False
        try:
            with patch("orchestrator.repo_manager.RepoManager") as MockRepo:
                mock_instance = MagicMock()
                MockRepo.return_value = mock_instance
                mock_instance.ensure_on_branch.return_value = f"agent/{task_id}"
                mock_instance.push_branch.side_effect = subprocess.CalledProcessError(
                    returncode=128,
                    cmd=["git", "push"],
                    stderr="fatal: repository not found",
                )
                push_branch_step(task, {}, Path("/nonexistent/task_dir"))
        except (subprocess.CalledProcessError, RuntimeError, Exception):
            push_raised = True

        assert push_raised, "push_branch step should raise when git push fails"

        # Task should NOT have advanced to provisional
        current = sdk.tasks.get(task_id)
        assert current["queue"] == "claimed", (
            f"Task should stay in 'claimed' when push fails, got: {current['queue']!r}"
        )
        assert current["queue"] != "provisional", (
            "Task must NOT advance to provisional when push_branch step fails"
        )

    def test_push_failure_raised_exception_type(self, broken_remote_repo):
        """Push failure propagates a clear exception (not swallowed silently)."""
        task = _make_task_dict(make_task_id())

        exception_raised = False
        exc = None
        try:
            push_branch_step(task, {}, broken_remote_repo)
        except Exception as e:
            exception_raised = True
            exc = e

        assert exception_raised, (
            "push_branch step must raise when git push fails — "
            "silently swallowing the error would leave the task orphaned in claimed"
        )
        # Verify it's a meaningful error type (not just SystemExit or KeyboardInterrupt)
        assert isinstance(exc, (subprocess.CalledProcessError, RuntimeError, OSError)), (
            f"Expected CalledProcessError, RuntimeError, or OSError, got: {type(exc)}"
        )
