"""Tests for orchestrator.git_utils module."""

import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestGetCommitCount:
    """Tests for get_commit_count function."""

    def test_get_commit_count_with_commits(self, temp_dir):
        """Test counting commits when there are commits."""
        from orchestrator.git_utils import get_commit_count

        # Mock the git commands
        with patch('orchestrator.git_utils.run_git') as mock_run:
            # Mock merge-base finding common ancestor
            merge_base_result = MagicMock()
            merge_base_result.returncode = 0
            merge_base_result.stdout = "abc123\n"

            # Mock rev-list counting commits
            count_result = MagicMock()
            count_result.returncode = 0
            count_result.stdout = "5\n"

            mock_run.side_effect = [merge_base_result, count_result]

            count = get_commit_count(temp_dir)

            assert count == 5

    def test_get_commit_count_no_common_ancestor(self, temp_dir):
        """Test counting commits when there's no common ancestor with main."""
        from orchestrator.git_utils import get_commit_count

        with patch('orchestrator.git_utils.run_git') as mock_run:
            # Mock merge-base failing (no common ancestor)
            merge_base_result = MagicMock()
            merge_base_result.returncode = 1

            # Mock rev-list counting all commits
            count_result = MagicMock()
            count_result.returncode = 0
            count_result.stdout = "10\n"

            mock_run.side_effect = [merge_base_result, count_result]

            count = get_commit_count(temp_dir)

            assert count == 10

    def test_get_commit_count_with_since_ref(self, temp_dir):
        """Test counting commits since a specific ref."""
        from orchestrator.git_utils import get_commit_count

        with patch('orchestrator.git_utils.run_git') as mock_run:
            count_result = MagicMock()
            count_result.returncode = 0
            count_result.stdout = "3\n"

            mock_run.return_value = count_result

            count = get_commit_count(temp_dir, since_ref="HEAD~5")

            assert count == 3
            # Should have called with the since_ref
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert "HEAD~5..HEAD" in call_args

    def test_get_commit_count_zero(self, temp_dir):
        """Test counting commits when there are none."""
        from orchestrator.git_utils import get_commit_count

        with patch('orchestrator.git_utils.run_git') as mock_run:
            merge_base_result = MagicMock()
            merge_base_result.returncode = 0
            merge_base_result.stdout = "abc123\n"

            count_result = MagicMock()
            count_result.returncode = 0
            count_result.stdout = "0\n"

            mock_run.side_effect = [merge_base_result, count_result]

            count = get_commit_count(temp_dir)

            assert count == 0

    def test_get_commit_count_error(self, temp_dir):
        """Test that errors return 0."""
        from orchestrator.git_utils import get_commit_count

        with patch('orchestrator.git_utils.run_git') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "git")

            count = get_commit_count(temp_dir)

            assert count == 0

    def test_get_commit_count_invalid_output(self, temp_dir):
        """Test handling invalid git output."""
        from orchestrator.git_utils import get_commit_count

        with patch('orchestrator.git_utils.run_git') as mock_run:
            merge_base_result = MagicMock()
            merge_base_result.returncode = 0
            merge_base_result.stdout = "abc123\n"

            count_result = MagicMock()
            count_result.returncode = 0
            count_result.stdout = "not a number\n"

            mock_run.side_effect = [merge_base_result, count_result]

            count = get_commit_count(temp_dir)

            assert count == 0


class TestRunGit:
    """Tests for run_git helper function."""

    def test_run_git_success(self, temp_dir):
        """Test successful git command."""
        from orchestrator.git_utils import run_git

        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "output\n"
            mock_run.return_value = mock_result

            result = run_git(["status"], cwd=temp_dir)

            assert result.returncode == 0
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][0] == ["git", "status"]

    def test_run_git_with_check(self, temp_dir):
        """Test that check=True raises on non-zero exit."""
        from orchestrator.git_utils import run_git

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "git status")

            with pytest.raises(subprocess.CalledProcessError):
                run_git(["status"], cwd=temp_dir, check=True)

    def test_run_git_without_check(self, temp_dir):
        """Test that check=False doesn't raise."""
        from orchestrator.git_utils import run_git

        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_run.return_value = mock_result

            result = run_git(["status"], cwd=temp_dir, check=False)

            assert result.returncode == 1


class TestHasUncommittedChanges:
    """Tests for has_uncommitted_changes function."""

    def test_has_uncommitted_changes_clean(self, temp_dir):
        """Test clean working directory."""
        from orchestrator.git_utils import has_uncommitted_changes

        with patch('orchestrator.git_utils.run_git') as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_run.return_value = mock_result

            has_changes = has_uncommitted_changes(temp_dir)

            assert has_changes is False

    def test_has_uncommitted_changes_dirty(self, temp_dir):
        """Test dirty working directory."""
        from orchestrator.git_utils import has_uncommitted_changes

        with patch('orchestrator.git_utils.run_git') as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = "M file.py\n"
            mock_run.return_value = mock_result

            has_changes = has_uncommitted_changes(temp_dir)

            assert has_changes is True
