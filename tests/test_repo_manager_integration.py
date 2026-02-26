"""Integration tests for RepoManager.rebase_on_base using real git operations.

These tests exercise real subprocess git calls on temporary repositories.
No mocking of _run_git or subprocess is used.
"""

import subprocess
from pathlib import Path

import pytest

from octopoid.repo_manager import RebaseStatus, RepoManager


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a git command in a directory, raising on failure."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _current_branch(cwd: Path) -> str:
    """Return the current branch name in the given directory."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _head_sha(cwd: Path) -> str:
    """Return the current HEAD SHA in the given directory."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class TestRebaseOnBaseIntegration:
    def test_up_to_date(self, test_repo: dict) -> None:
        """When HEAD is already up to date with origin/base, returns UP_TO_DATE."""
        work = test_repo["work"]
        base_branch = _current_branch(work)

        repo = RepoManager(worktree=work, base_branch=base_branch)
        result = repo.rebase_on_base()

        assert result.status == RebaseStatus.UP_TO_DATE

    def test_successful_rebase(self, test_repo: dict) -> None:
        """When task branch is behind base (no conflict), rebase succeeds and HEAD is rebased."""
        work = test_repo["work"]
        base_branch = _current_branch(work)

        # Create a task branch with a commit on a unique file (no conflict)
        _git(["checkout", "-b", "task-branch"], work)
        (work / "task-file.txt").write_text("task content\n")
        _git(["add", "task-file.txt"], work)
        _git(["commit", "-m", "task: add task-file.txt"], work)
        pre_rebase_head = _head_sha(work)

        # Add a new commit to the base branch on the remote (different file — no conflict)
        _git(["checkout", base_branch], work)
        (work / "base-extra.txt").write_text("base update\n")
        _git(["add", "base-extra.txt"], work)
        _git(["commit", "-m", "base: add base-extra.txt"], work)
        _git(["push", "origin", base_branch], work)

        # Read the SHA of the new remote base tip (before fetch inside rebase_on_base)
        origin_base_sha = _head_sha(work)

        # Return to task branch — it is now 1 commit behind origin/base
        _git(["checkout", "task-branch"], work)

        repo = RepoManager(worktree=work, base_branch=base_branch)
        result = repo.rebase_on_base()

        assert result.status == RebaseStatus.SUCCESS

        # HEAD should have changed (old commit was rebased onto the new base)
        new_head = _head_sha(work)
        assert new_head != pre_rebase_head

        # Parent of the rebased commit must be the new base tip
        parent = subprocess.run(
            ["git", "rev-parse", "HEAD~1"],
            cwd=work,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert parent == origin_base_sha

    def test_conflict_aborts_cleanly(self, conflicting_repo: dict) -> None:
        """On rebase conflict, aborts and returns CONFLICT; no lingering rebase state."""
        work = conflicting_repo["work"]

        # After conflicting_repo, work is on base_branch — record it before switching
        base_branch = _current_branch(work)

        # Switch to the task branch, which has a conflicting change
        _git(["checkout", "task-branch"], work)

        repo = RepoManager(worktree=work, base_branch=base_branch)
        result = repo.rebase_on_base()

        assert result.status == RebaseStatus.CONFLICT

        # The abort must have cleaned up all in-progress rebase state
        rebase_merge = work / ".git" / "rebase-merge"
        rebase_apply = work / ".git" / "rebase-apply"
        assert not rebase_merge.exists(), (
            f"rebase-merge dir should not exist after abort: {rebase_merge}"
        )
        assert not rebase_apply.exists(), (
            f"rebase-apply dir should not exist after abort: {rebase_apply}"
        )
