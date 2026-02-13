"""Unit tests for execution notes generation."""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from orchestrator.queue_utils import _generate_execution_notes


class TestGenerateExecutionNotes:
    """Test the _generate_execution_notes function."""

    def test_basic_execution_notes(self):
        """Generate basic execution notes with commits and turns."""
        task_info = {"id": "test-123", "title": "Test Task"}

        notes = _generate_execution_notes(
            task_info=task_info,
            commits_count=3,
            turns_used=5
        )

        assert "Created 3 commits" in notes
        assert "5 turns used" in notes

    def test_single_commit_singular(self):
        """Use singular 'commit' for count of 1."""
        task_info = {"id": "test-123"}

        notes = _generate_execution_notes(
            task_info=task_info,
            commits_count=1,
            turns_used=1
        )

        assert "Created 1 commit" in notes
        assert "1 turn used" in notes
        # Should NOT have plural forms
        assert "commits" not in notes
        assert "turns" not in notes

    def test_no_commits(self):
        """Handle zero commits gracefully."""
        task_info = {"id": "test-123"}

        notes = _generate_execution_notes(
            task_info=task_info,
            commits_count=0,
            turns_used=10
        )

        assert "No commits made" in notes
        assert "10 turns used" in notes

    def test_no_turns_provided(self):
        """Turns are optional - can be None."""
        task_info = {"id": "test-123"}

        notes = _generate_execution_notes(
            task_info=task_info,
            commits_count=2,
            turns_used=None
        )

        assert "Created 2 commits" in notes
        assert "turn" not in notes.lower()  # No turn info should be present

    def test_includes_commit_messages(self, tmp_path):
        """Include recent commit messages in notes."""
        # Create a git repo with commits
        repo_dir = tmp_path / "test-repo"
        repo_dir.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)

        # Create commits
        for i in range(3):
            test_file = repo_dir / f"file{i}.txt"
            test_file.write_text(f"content {i}")
            subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"feat: add feature {i}"],
                cwd=repo_dir,
                check=True,
                capture_output=True
            )

        # Mock cwd to be inside the git repo
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(repo_dir)

            task_info = {"id": "test-123"}
            notes = _generate_execution_notes(
                task_info=task_info,
                commits_count=3,
                turns_used=5
            )

            # Should include commit messages
            assert "Changes:" in notes
            assert "feat: add feature" in notes

        finally:
            os.chdir(original_cwd)

    def test_limits_commit_messages_to_five(self, tmp_path):
        """Only include up to 5 most recent commits in summary."""
        repo_dir = tmp_path / "test-repo"
        repo_dir.mkdir()

        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)

        # Create 10 commits
        for i in range(10):
            test_file = repo_dir / f"file{i}.txt"
            test_file.write_text(f"content {i}")
            subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"commit {i}"],
                cwd=repo_dir,
                check=True,
                capture_output=True
            )

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(repo_dir)

            task_info = {"id": "test-123"}
            notes = _generate_execution_notes(
                task_info=task_info,
                commits_count=10,
                turns_used=15
            )

            # Should request only 5 commits (the function calls git log -n 5)
            # Most recent commits should be included
            assert "commit 9" in notes
            assert "commit 8" in notes
            assert "commit 7" in notes
            assert "commit 6" in notes
            assert "commit 5" in notes

        finally:
            os.chdir(original_cwd)

    def test_graceful_failure_when_not_in_git_repo(self, tmp_path):
        """Handle gracefully when not in a git repository."""
        import os
        original_cwd = os.getcwd()
        try:
            # Change to a directory that's not a git repo
            non_repo_dir = tmp_path / "not-a-repo"
            non_repo_dir.mkdir()
            os.chdir(non_repo_dir)

            task_info = {"id": "test-123"}
            notes = _generate_execution_notes(
                task_info=task_info,
                commits_count=3,
                turns_used=5
            )

            # Should still generate basic notes without commit messages
            assert "Created 3 commits" in notes
            assert "5 turns used" in notes
            # Should NOT include Changes section
            assert "Changes:" not in notes

        finally:
            os.chdir(original_cwd)

    def test_output_format(self):
        """Verify output is properly formatted with periods."""
        task_info = {"id": "test-123"}

        notes = _generate_execution_notes(
            task_info=task_info,
            commits_count=2,
            turns_used=3
        )

        # Should be sentence-formatted with periods
        assert notes.endswith(".")
        # Parts should be joined with ". "
        assert "commits. 3 turns" in notes
