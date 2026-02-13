"""Tests for RepoManager."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.repo_manager import (
    PrInfo,
    RebaseResult,
    RebaseStatus,
    RepoManager,
    RepoStatus,
)


@pytest.fixture
def worktree(tmp_path):
    """Create a fake worktree directory."""
    wt = tmp_path / "worktree"
    wt.mkdir()
    return wt


@pytest.fixture
def repo(worktree):
    """Create a RepoManager with a fake worktree."""
    return RepoManager(worktree=worktree, base_branch="main")


def make_completed(stdout="", stderr="", returncode=0):
    """Helper to create a CompletedProcess."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestGetStatus:
    def test_returns_status(self, repo):
        """get_status returns branch, commits, uncommitted, and head ref."""
        with patch.object(repo, "_run_git") as mock_git:
            mock_git.side_effect = [
                make_completed(stdout="feature/test\n"),  # rev-parse --abbrev-ref
                make_completed(stdout="abc123\n"),  # rev-parse HEAD
                make_completed(stdout="3\n"),  # rev-list --count
                make_completed(stdout=" M file.py\n"),  # status --porcelain
            ]
            status = repo.get_status()

        assert status.branch == "feature/test"
        assert status.head_ref == "abc123"
        assert status.commits_ahead == 3
        assert status.has_uncommitted is True

    def test_clean_worktree(self, repo):
        """get_status reports no uncommitted changes when worktree is clean."""
        with patch.object(repo, "_run_git") as mock_git:
            mock_git.side_effect = [
                make_completed(stdout="main\n"),
                make_completed(stdout="def456\n"),
                make_completed(stdout="0\n"),
                make_completed(stdout=""),
            ]
            status = repo.get_status()

        assert status.has_uncommitted is False
        assert status.commits_ahead == 0

    def test_handles_errors_gracefully(self, repo):
        """get_status returns defaults when git commands fail."""
        with patch.object(repo, "_run_git") as mock_git:
            mock_git.return_value = make_completed(returncode=1)
            status = repo.get_status()

        assert status.branch == ""
        assert status.head_ref == ""
        assert status.commits_ahead == 0
        assert status.has_uncommitted is False


class TestPushBranch:
    def test_pushes_current_branch(self, repo):
        """push_branch pushes the current branch to origin."""
        with patch.object(repo, "get_status") as mock_status, \
             patch.object(repo, "_run_git") as mock_git:
            mock_status.return_value = RepoStatus(
                branch="agent/task-1", commits_ahead=2,
                has_uncommitted=False, head_ref="abc"
            )
            mock_git.return_value = make_completed()

            branch = repo.push_branch()

        assert branch == "agent/task-1"
        mock_git.assert_called_once_with(["push", "-u", "origin", "agent/task-1"])

    def test_force_push(self, repo):
        """push_branch with force uses --force-with-lease."""
        with patch.object(repo, "get_status") as mock_status, \
             patch.object(repo, "_run_git") as mock_git:
            mock_status.return_value = RepoStatus(
                branch="agent/task-1", commits_ahead=2,
                has_uncommitted=False, head_ref="abc"
            )
            mock_git.return_value = make_completed()

            repo.push_branch(force=True)

        mock_git.assert_called_once_with(
            ["push", "--force-with-lease", "-u", "origin", "agent/task-1"]
        )


class TestRebaseOnBase:
    def test_success(self, repo):
        """Successful rebase returns SUCCESS."""
        with patch.object(repo, "_run_git") as mock_git:
            mock_git.side_effect = [
                make_completed(),  # fetch
                make_completed(stdout="2\n"),  # rev-list (behind by 2)
                make_completed(),  # rebase
            ]
            result = repo.rebase_on_base()

        assert result.status == RebaseStatus.SUCCESS
        assert "Rebased" in result.message

    def test_up_to_date(self, repo):
        """When already up to date, returns UP_TO_DATE."""
        with patch.object(repo, "_run_git") as mock_git:
            mock_git.side_effect = [
                make_completed(),  # fetch
                make_completed(stdout="0\n"),  # rev-list (0 behind)
            ]
            result = repo.rebase_on_base()

        assert result.status == RebaseStatus.UP_TO_DATE

    def test_conflict(self, repo):
        """On rebase conflict, aborts and returns CONFLICT."""
        with patch.object(repo, "_run_git") as mock_git:
            mock_git.side_effect = [
                make_completed(),  # fetch
                make_completed(stdout="1\n"),  # rev-list (behind by 1)
                subprocess.CalledProcessError(
                    1, "git rebase", stderr="CONFLICT in file.py"
                ),  # rebase fails
                make_completed(),  # rebase --abort
            ]
            result = repo.rebase_on_base()

        assert result.status == RebaseStatus.CONFLICT
        assert "CONFLICT" in result.conflict_output

    def test_fetch_failure(self, repo):
        """When fetch fails, returns ERROR."""
        with patch.object(repo, "_run_git") as mock_git:
            mock_git.side_effect = subprocess.CalledProcessError(
                1, "git fetch", stderr="network error"
            )
            result = repo.rebase_on_base()

        assert result.status == RebaseStatus.ERROR
        assert "fetch" in result.message.lower()


class TestCreatePr:
    def test_creates_new_pr(self, repo):
        """create_pr pushes branch and creates a new PR."""
        with patch.object(repo, "push_branch") as mock_push, \
             patch.object(repo, "_run_gh") as mock_gh:
            mock_push.return_value = "agent/task-1"
            mock_gh.side_effect = [
                make_completed(returncode=1),  # pr view (no existing PR)
                make_completed(stdout="https://github.com/org/repo/pull/42\n"),  # pr create
            ]

            pr = repo.create_pr(title="[task-1] Fix bug")

        assert pr.url == "https://github.com/org/repo/pull/42"
        assert pr.number == 42
        assert pr.created is True

    def test_returns_existing_pr(self, repo):
        """create_pr returns existing PR info if one already exists."""
        with patch.object(repo, "push_branch") as mock_push, \
             patch.object(repo, "_run_gh") as mock_gh:
            mock_push.return_value = "agent/task-1"
            mock_gh.return_value = make_completed(
                stdout="https://github.com/org/repo/pull/10 10\n"
            )

            pr = repo.create_pr(title="[task-1] Fix bug")

        assert pr.url == "https://github.com/org/repo/pull/10"
        assert pr.number == 10
        assert pr.created is False

    def test_pr_with_body(self, repo):
        """create_pr passes body to gh pr create."""
        with patch.object(repo, "push_branch") as mock_push, \
             patch.object(repo, "_run_gh") as mock_gh:
            mock_push.return_value = "agent/task-1"
            mock_gh.side_effect = [
                make_completed(returncode=1),  # no existing PR
                make_completed(stdout="https://github.com/org/repo/pull/5\n"),
            ]

            repo.create_pr(title="Title", body="## Summary\nFixes stuff")

        # Check the second call (pr create) includes --body
        create_call = mock_gh.call_args_list[1]
        args = create_call[0][0]
        assert "--body" in args
        body_idx = args.index("--body")
        assert args[body_idx + 1] == "## Summary\nFixes stuff"


class TestMergePr:
    def test_merge_success(self, repo):
        """merge_pr returns True on success."""
        with patch.object(repo, "_run_gh") as mock_gh:
            mock_gh.return_value = make_completed()
            assert repo.merge_pr(42) is True

    def test_merge_failure(self, repo):
        """merge_pr returns False on failure."""
        with patch.object(repo, "_run_gh") as mock_gh:
            mock_gh.return_value = make_completed(returncode=1)
            assert repo.merge_pr(42) is False

    def test_squash_merge(self, repo):
        """merge_pr passes the merge method to gh."""
        with patch.object(repo, "_run_gh") as mock_gh:
            mock_gh.return_value = make_completed()
            repo.merge_pr(42, method="squash")

        mock_gh.assert_called_once_with(
            ["pr", "merge", "42", "--squash"],
            check=False,
            timeout=60,
        )


class TestResetToBase:
    def test_resets_to_origin_base(self, repo):
        """reset_to_base fetches and hard resets."""
        with patch.object(repo, "_run_git") as mock_git:
            mock_git.return_value = make_completed()
            repo.reset_to_base()

        calls = [c[0][0] for c in mock_git.call_args_list]
        assert calls[0] == ["fetch", "origin", "main"]
        assert calls[1] == ["reset", "--hard", "origin/main"]


class TestSubmodule:
    def test_push_submodule_nothing_to_push(self, repo, worktree):
        """push_submodule returns True when nothing to push."""
        sub = worktree / "orchestrator"
        sub.mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                make_completed(stdout=""),  # status --porcelain (clean)
                make_completed(stdout="", returncode=1),  # rev-list (no unpushed)
            ]
            assert repo.push_submodule("orchestrator") is True

    def test_push_submodule_missing(self, repo):
        """push_submodule returns False if submodule doesn't exist."""
        assert repo.push_submodule("nonexistent") is False

    def test_stage_submodule_pointer(self, repo):
        """stage_submodule_pointer runs git add."""
        with patch.object(repo, "_run_git") as mock_git:
            mock_git.return_value = make_completed()
            assert repo.stage_submodule_pointer("orchestrator") is True

        mock_git.assert_called_once_with(["add", "orchestrator"], check=False)
