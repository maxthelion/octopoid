"""Tests for ephemeral task-scoped worktrees."""

import pytest
from pathlib import Path

# Note: These imports will work when run from the orchestrator/ directory
# with the proper PYTHONPATH setup
try:
    from orchestrator.git_utils import (
        create_task_worktree,
        cleanup_task_worktree,
        get_task_worktree_path,
        get_task_branch,
    )
except ImportError:
    # Fallback for different import contexts
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from orchestrator.git_utils import (
        create_task_worktree,
        cleanup_task_worktree,
        get_task_worktree_path,
        get_task_branch,
    )


class TestTaskWorktreePath:
    """Tests for get_task_worktree_path()."""

    def test_returns_path_under_tasks_dir(self):
        """Task worktree path should be .orchestrator/tasks/<id>/worktree/."""
        path = get_task_worktree_path("abc12345")
        assert path.name == "worktree"
        assert path.parent.name == "abc12345"
        assert "tasks" in path.parts


class TestGetTaskBranch:
    """Tests for get_task_branch() branch selection logic."""

    def test_project_task_uses_project_branch(self, db_with_project):
        """Project tasks should use the project's branch."""
        # This would need a DB fixture with a project
        # For now, just test the standalone logic
        pass

    def test_breakdown_task_uses_breakdown_branch(self):
        """Breakdown tasks should use breakdown/<breakdown_id> branch."""
        task = {
            "id": "abc12345",
            "breakdown_id": "breakdown-xyz",
            "role": "implement",
        }
        branch = get_task_branch(task)
        assert branch == "breakdown/breakdown-xyz"

    def test_orchestrator_impl_uses_orch_branch(self):
        """Orchestrator_impl tasks should use orch/<id> branch."""
        task = {
            "id": "abc12345",
            "role": "orchestrator_impl",
        }
        branch = get_task_branch(task)
        assert branch == "orch/abc12345"

    def test_regular_task_uses_agent_branch(self):
        """Regular tasks should use agent/<id> branch (no timestamp)."""
        task = {
            "id": "abc12345",
            "role": "implement",
        }
        branch = get_task_branch(task)
        assert branch == "agent/abc12345"


class TestCreateTaskWorktree:
    """Tests for create_task_worktree()."""

    def test_creates_worktree_at_correct_path(self, temp_git_repo):
        """Task worktree should be created at .orchestrator/tasks/<id>/worktree/."""
        task = {
            "id": "test-task",
            "role": "implement",
        }
        # Would need temp_git_repo fixture
        # For now this is a placeholder
        pass

    def test_creates_branch_if_not_exists_on_origin(self, temp_git_repo):
        """Should create branch from origin/main if it doesn't exist."""
        pass

    def test_uses_existing_branch_from_origin(self, temp_git_repo):
        """Should checkout existing branch if it exists on origin."""
        pass

    def test_uses_existing_local_branch_when_checked_out(self):
        """Should use existing local branch even if it's checked out elsewhere.

        This tests the fix for when a project branch is already checked out
        in the parent repo and a task tries to create a worktree for it.
        """
        import subprocess
        import tempfile
        import shutil
        from pathlib import Path
        from unittest.mock import patch

        # Create a temporary git repository
        temp_dir = Path(tempfile.mkdtemp())
        try:
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=temp_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=temp_dir, check=True, capture_output=True)

            # Create initial commit on main
            (temp_dir / "README.md").write_text("# Test Repo")
            subprocess.run(["git", "add", "README.md"], cwd=temp_dir, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=temp_dir, check=True, capture_output=True)

            # Create and checkout a feature branch (simulating project branch)
            subprocess.run(["git", "checkout", "-b", "feature/test-branch"], cwd=temp_dir, check=True, capture_output=True)

            # Create .octopoid/tasks directory structure
            tasks_dir = temp_dir / ".octopoid" / "tasks"
            tasks_dir.mkdir(parents=True, exist_ok=True)

            # Mock the config functions to point to our temp repo
            with patch('orchestrator.config.find_parent_project', return_value=temp_dir):
                with patch('orchestrator.config.get_tasks_dir', return_value=tasks_dir):
                    # Create a task that should use the feature branch
                    task = {
                        "id": "test123",
                        "role": "implement",
                    }

                    # Mock get_task_branch to return the checked-out branch
                    with patch('orchestrator.git_utils.get_task_branch', return_value="feature/test-branch"):
                        # This should NOT fail even though feature/test-branch is checked out
                        worktree_path = create_task_worktree(task)

                        # Verify worktree was created
                        assert worktree_path.exists()
                        assert (worktree_path / ".git").exists()

                        # Verify it's on the correct branch
                        result = subprocess.run(
                            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            cwd=worktree_path,
                            capture_output=True,
                            text=True,
                            check=True,
                        )
                        assert result.stdout.strip() == "feature/test-branch"

        finally:
            # Clean up
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestCleanupTaskWorktree:
    """Tests for cleanup_task_worktree()."""

    def test_pushes_commits_before_deletion(self, temp_git_repo):
        """Should push unpushed commits before deleting worktree."""
        pass

    def test_skips_push_when_push_commits_false(self, temp_git_repo):
        """Should skip pushing if push_commits=False."""
        pass

    def test_removes_worktree_directory(self, temp_git_repo):
        """Should remove the worktree directory."""
        pass

    def test_handles_nonexistent_worktree_gracefully(self):
        """Should not error if worktree doesn't exist."""
        cleanup_task_worktree("nonexistent-task", push_commits=False)
        # Should not raise


class TestTaskWorktreeIntegration:
    """Integration tests for task worktree lifecycle."""

    def test_project_task_sequence(self, temp_git_repo):
        """Multiple tasks in same project should see each other's commits via origin.

        Flow:
        1. Task A claims, creates worktree, commits, pushes, cleanup
        2. Task B claims, creates worktree from origin (includes Task A's commits)
        3. Task B sees Task A's work
        """
        pass

    def test_cleanup_on_task_completion(self, temp_git_repo):
        """Worktree should be cleaned up when task completes."""
        pass

    def test_cleanup_on_task_failure(self, temp_git_repo):
        """Worktree should be cleaned up even when task fails."""
        pass

    def test_worktree_isolation_between_tasks(self, temp_git_repo):
        """Different tasks should get completely independent worktrees."""
        pass


# Fixtures would need to be implemented for temp git repos, DB setup, etc.
