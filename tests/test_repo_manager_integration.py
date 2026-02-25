"""Integration tests for RepoManager.rebase_on_base() using real git operations.

All scenarios use real subprocess git calls against temporary local repos.
No mocking of _run_git or subprocess.
"""

import subprocess
from pathlib import Path

import pytest

from orchestrator.repo_manager import RebaseStatus, RepoManager


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
    """Return the name of the current branch in the given directory."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class TestRebaseOnBaseIntegration:
    def test_already_up_to_date(self, test_repo):
        """Rebase when already current with origin returns UP_TO_DATE."""
        work = test_repo["work"]
        base = _current_branch(work)

        repo = RepoManager(worktree=work, base_branch=base)
        result = repo.rebase_on_base()

        assert result.status == RebaseStatus.UP_TO_DATE

    def test_successful_rebase(self, test_repo, tmp_path):
        """Rebase a task branch onto an advanced base returns SUCCESS."""
        work = test_repo["work"]
        bare = test_repo["bare"]
        base = _current_branch(work)

        # Create task branch with a non-conflicting commit
        _git(["checkout", "-b", "task-branch"], work)
        (work / "task-file.txt").write_text("task content\n")
        _git(["add", "task-file.txt"], work)
        _git(["commit", "-m", "task: add task-file.txt"], work)

        # Advance base branch via a second clone so task-branch is now behind
        second = tmp_path / "second"
        subprocess.run(
            ["git", "clone", str(bare), str(second)],
            check=True,
            capture_output=True,
        )
        _git(["config", "user.email", "test@example.com"], second)
        _git(["config", "user.name", "Test"], second)
        (second / "base-advance.txt").write_text("base advance\n")
        _git(["add", "base-advance.txt"], second)
        _git(["commit", "-m", "base: advance base branch"], second)
        _git(["push", "origin", base], second)

        # work is on task-branch, origin/base is now 1 commit ahead — rebase should succeed
        repo = RepoManager(worktree=work, base_branch=base)
        result = repo.rebase_on_base()

        assert result.status == RebaseStatus.SUCCESS

        # After rebase, task-branch should be ahead of origin/base (our commit on top)
        ahead = subprocess.run(
            ["git", "rev-list", "--count", f"origin/{base}..HEAD"],
            cwd=work,
            capture_output=True,
            text=True,
            check=True,
        )
        assert int(ahead.stdout.strip()) > 0

    def test_conflict_aborts_cleanly(self, conflicting_repo, tmp_path):
        """Rebase with a real conflict aborts and returns CONFLICT with clean git state."""
        bare = conflicting_repo["bare"]
        work = conflicting_repo["work"]

        # conflicting_repo leaves work on base_branch after making the conflicting commit
        base = _current_branch(work)

        # Clone from bare into a fresh worktree and check out task-branch
        task_wt = tmp_path / "task-worktree"
        subprocess.run(
            ["git", "clone", str(bare), str(task_wt)],
            check=True,
            capture_output=True,
        )
        _git(["config", "user.email", "test@example.com"], task_wt)
        _git(["config", "user.name", "Test"], task_wt)
        _git(["checkout", "task-branch"], task_wt)

        repo = RepoManager(worktree=task_wt, base_branch=base)
        result = repo.rebase_on_base()

        assert result.status == RebaseStatus.CONFLICT

        # Abort ran correctly — no leftover rebase state
        assert not (task_wt / ".git" / "rebase-merge").exists()
