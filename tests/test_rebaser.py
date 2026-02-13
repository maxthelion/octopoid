"""Tests for the rebaser system: scheduler staleness check and worktree management."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Scheduler: staleness checking
# ---------------------------------------------------------------------------


class TestStalenessChecking:
    """Tests for the scheduler's branch staleness detection."""

    def test_count_commits_behind(self):
        """_count_commits_behind returns correct count."""
        from orchestrator.scheduler import _count_commits_behind

        with patch("subprocess.run") as mock_run:
            # Mock: branch exists (rev-parse succeeds)
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="abc123\n"),  # rev-parse
                MagicMock(returncode=0, stdout="7\n"),  # rev-list --count
            ]

            result = _count_commits_behind(Path("/fake/repo"), "feature/test")
            assert result == 7

    def test_count_commits_behind_branch_not_found(self):
        """_count_commits_behind returns None when branch doesn't exist."""
        from orchestrator.scheduler import _count_commits_behind

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            result = _count_commits_behind(Path("/fake/repo"), "no-such-branch")
            assert result is None


# ---------------------------------------------------------------------------
# Scheduler: branch freshness checking (merge-base --is-ancestor)
# ---------------------------------------------------------------------------


class TestBranchFreshnessChecking:
    """Tests for _is_branch_fresh() and check_branch_freshness()."""

    def test_is_branch_fresh_returns_true_for_fresh_branch(self):
        """_is_branch_fresh returns True when main is ancestor of branch."""
        from orchestrator.scheduler import _is_branch_fresh

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="abc123\n"),  # rev-parse succeeds
                MagicMock(returncode=0),  # merge-base --is-ancestor returns 0 (is ancestor)
            ]

            result = _is_branch_fresh(Path("/fake/repo"), "feature/test")
            assert result is True

    def test_is_branch_fresh_returns_false_for_stale_branch(self):
        """_is_branch_fresh returns False when main is NOT ancestor of branch."""
        from orchestrator.scheduler import _is_branch_fresh

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="abc123\n"),  # rev-parse succeeds
                MagicMock(returncode=1),  # merge-base --is-ancestor returns 1 (not ancestor)
            ]

            result = _is_branch_fresh(Path("/fake/repo"), "feature/old")
            assert result is False

    def test_is_branch_fresh_returns_none_for_missing_branch(self):
        """_is_branch_fresh returns None when branch doesn't exist."""
        from orchestrator.scheduler import _is_branch_fresh

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")  # rev-parse fails
            result = _is_branch_fresh(Path("/fake/repo"), "no-such-branch")
            assert result is None

    def test_is_branch_fresh_returns_none_on_timeout(self):
        """_is_branch_fresh returns None when git times out."""
        from orchestrator.scheduler import _is_branch_fresh

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=10)
            result = _is_branch_fresh(Path("/fake/repo"), "feature/test")
            assert result is None


# ---------------------------------------------------------------------------
# Scheduler: ensure_rebaser_worktree
# ---------------------------------------------------------------------------


class TestEnsureRebaserWorktree:
    """Tests for ensure_rebaser_worktree()."""

    def test_returns_existing_worktree(self, mock_config, temp_dir):
        """Returns path if worktree already exists."""
        from orchestrator.scheduler import ensure_rebaser_worktree

        worktree_path = mock_config / "runtime" / "agents" / "rebaser-worktree"
        worktree_path.mkdir(parents=True, exist_ok=True)
        (worktree_path / ".git").write_text("gitdir: /fake")

        result = ensure_rebaser_worktree()
        assert result == worktree_path

    def test_creates_worktree_if_missing(self, mock_config, temp_dir):
        """Creates worktree using git worktree add when it doesn't exist."""
        from orchestrator.scheduler import ensure_rebaser_worktree

        worktree_path = mock_config / "runtime" / "agents" / "rebaser-worktree"

        with patch("subprocess.run") as mock_run:
            # First call: git worktree add succeeds
            def side_effect(*args, **kwargs):
                cmd = args[0] if args else kwargs.get("args", [])
                if "worktree" in cmd:
                    # Create the dir and .git to simulate success
                    worktree_path.mkdir(parents=True, exist_ok=True)
                    (worktree_path / ".git").write_text("gitdir: /fake")
                    return MagicMock(returncode=0, stdout="", stderr="")
                return MagicMock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect
            result = ensure_rebaser_worktree()

        # The function should have been called (for git worktree add + npm install)
        assert mock_run.called

    def test_returns_none_on_git_failure(self, mock_config, temp_dir):
        """Returns None if git worktree add fails."""
        from orchestrator.scheduler import ensure_rebaser_worktree

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="fatal: error"
            )
            result = ensure_rebaser_worktree()
            assert result is None
