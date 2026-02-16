"""Integration test for 2-task project lifecycle.

This test verifies the complete flow documented in draft 21:
- Project creation with shared branch
- Task 1 execution (worktree, commits, completion)
- Task 1 cleanup (worktree detached, commits pushed)
- Task 2 execution (sees task 1 commits)
- Proper worktree lifecycle (detached, not deleted)

This test uses real git operations but doesn't require the API server.
"""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.git_utils import (
    create_task_worktree,
    cleanup_task_worktree,
    run_git,
)


class TestProjectLifecycle:
    """Integration test for 2-task project sharing a branch."""

    @pytest.fixture
    def bare_repo(self, tmp_path):
        """Create a local bare repo as 'origin' for testing."""
        bare_path = tmp_path / "bare.git"
        bare_path.mkdir()

        # Initialize bare repo
        subprocess.run(
            ["git", "init", "--bare"],
            cwd=bare_path,
            check=True,
            capture_output=True,
        )

        # Create a working clone to set up initial state
        work_path = tmp_path / "setup_work"
        subprocess.run(
            ["git", "clone", str(bare_path), str(work_path)],
            check=True,
            capture_output=True,
        )

        # Configure the working clone
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=work_path,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=work_path,
            check=True,
        )

        # Create initial commit on main
        (work_path / "README.md").write_text("# Test Repo")
        subprocess.run(["git", "add", "README.md"], cwd=work_path, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=work_path,
            check=True,
        )
        subprocess.run(["git", "push", "origin", "main"], cwd=work_path, check=True)

        # Create feature branch from main
        subprocess.run(
            ["git", "checkout", "-b", "feature/test-branch"],
            cwd=work_path,
            check=True,
        )
        (work_path / "feature.txt").write_text("Feature branch base")
        subprocess.run(["git", "add", "feature.txt"], cwd=work_path, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Feature branch base"],
            cwd=work_path,
            check=True,
        )
        subprocess.run(
            ["git", "push", "origin", "feature/test-branch"],
            cwd=work_path,
            check=True,
        )

        # Clean up working clone
        shutil.rmtree(work_path)

        return bare_path

    @pytest.fixture
    def project_repo(self, tmp_path, bare_repo):
        """Create a repo clone that acts as the main octopoid repo."""
        repo_path = tmp_path / "octopoid_repo"
        subprocess.run(
            ["git", "clone", str(bare_repo), str(repo_path)],
            check=True,
            capture_output=True,
        )

        # Configure git
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_path,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo_path,
            check=True,
        )

        # Checkout feature branch
        subprocess.run(
            ["git", "checkout", "feature/test-branch"],
            cwd=repo_path,
            check=True,
        )

        return repo_path

    @pytest.fixture
    def mock_config(self, project_repo, tmp_path):
        """Mock config functions to use test paths."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)

        with patch("orchestrator.git_utils.find_parent_project", return_value=project_repo), \
             patch("orchestrator.git_utils.get_tasks_dir", return_value=tasks_dir), \
             patch("orchestrator.git_utils.get_main_branch", return_value="main"), \
             patch("orchestrator.config.find_parent_project", return_value=project_repo), \
             patch("orchestrator.config.get_tasks_dir", return_value=tasks_dir):
            yield {
                "project_repo": project_repo,
                "tasks_dir": tasks_dir,
            }

    def test_two_task_project_lifecycle(self, mock_config):
        """Full 2-task project lifecycle with shared branch.

        Verifies:
        1. Project tasks share the same branch
        2. Task 1 worktree is created from correct base branch
        3. Task 1 commits are made and pushed
        4. Task 1 worktree is detached (not deleted)
        5. Task 2 worktree is created and sees task 1 commits
        6. Both worktrees are detached after completion
        """
        project_repo = mock_config["project_repo"]
        tasks_dir = mock_config["tasks_dir"]

        # Step 1: Create a 2-task project
        # Create project tasks with shared branch (using dict, no SDK needed)
        project_id = "PROJ-test001"
        project_branch = "project/test-lifecycle"

        task1 = {
            "id": "TASK-proj-01",
            "file_path": f"/tmp/{project_id}-task1.md",
            "title": "Project Task 1",
            "role": "implement",
            "priority": "P1",
            "project_id": project_id,
            "branch": "feature/test-branch",  # Base branch for project
        }

        task2 = {
            "id": "TASK-proj-02",
            "file_path": f"/tmp/{project_id}-task2.md",
            "title": "Project Task 2",
            "role": "implement",
            "priority": "P1",
            "project_id": project_id,
            "branch": "feature/test-branch",  # Same base branch
        }

        # Mock get_project to return project with shared branch
        def mock_get_project(proj_id):
            if proj_id == project_id:
                return {
                    "id": project_id,
                    "branch": project_branch,
                    "base_branch": "feature/test-branch",
                    "status": "active",
                }
            return None

        with patch("orchestrator.queue_utils.get_project", side_effect=mock_get_project):
            # Step 2: Simulate task 1 execution
            worktree1 = create_task_worktree(task1)

            # Verify worktree exists and is on correct branch
            assert worktree1.exists()
            assert (worktree1 / ".git").exists()

            # Verify base branch - should be feature/test-branch, not main
            merge_base = run_git(
                ["merge-base", "HEAD", "origin/feature/test-branch"],
                cwd=worktree1,
            )
            head_commit = run_git(["rev-parse", "HEAD"], cwd=worktree1)
            # HEAD should be at or ahead of feature/test-branch
            assert merge_base.stdout.strip() in head_commit.stdout.strip()

            # Make a commit in task 1
            test_file = worktree1 / "task1.txt"
            test_file.write_text("Task 1 work")
            run_git(["add", "task1.txt"], cwd=worktree1)
            run_git(["commit", "-m", "Task 1: Add feature"], cwd=worktree1)

            # Get commit hash for later verification
            task1_commit = run_git(["rev-parse", "HEAD"], cwd=worktree1).stdout.strip()

            # Step 3: Complete task 1 and verify cleanup
            cleanup_result = cleanup_task_worktree(task1["id"], push_commits=True)
            assert cleanup_result is True

            # Verify worktree still exists (not deleted)
            assert worktree1.exists()

            # Verify HEAD is detached
            head_ref = run_git(
                ["rev-parse", "--abbrev-ref", "HEAD"],
                cwd=worktree1,
            ).stdout.strip()
            assert head_ref == "HEAD", f"Expected detached HEAD, got {head_ref}"

            # Verify commits were pushed to origin
            remote_log = run_git(
                ["log", f"origin/{project_branch}", "--oneline", "-1"],
                cwd=project_repo,
            )
            assert "Task 1: Add feature" in remote_log.stdout

            # Step 4: Simulate task 2 execution
            worktree2 = create_task_worktree(task2)

            # Verify worktree exists
            assert worktree2.exists()
            assert (worktree2 / ".git").exists()

            # Verify task 2 worktree sees task 1's commits
            log_output = run_git(["log", "--oneline"], cwd=worktree2)
            assert "Task 1: Add feature" in log_output.stdout, \
                "Task 2 worktree should see task 1 commits"

            # Verify the commit hash matches
            task2_log = run_git(["rev-list", "HEAD"], cwd=worktree2).stdout
            assert task1_commit in task2_log, \
                "Task 1 commit should be in task 2 history"

            # Verify task 1's file exists in task 2 worktree
            task1_file_in_task2 = worktree2 / "task1.txt"
            assert task1_file_in_task2.exists(), \
                "Task 1's file should exist in task 2 worktree"
            assert task1_file_in_task2.read_text() == "Task 1 work"

            # Make a commit in task 2
            test_file2 = worktree2 / "task2.txt"
            test_file2.write_text("Task 2 work")
            run_git(["add", "task2.txt"], cwd=worktree2)
            run_git(["commit", "-m", "Task 2: Add more features"], cwd=worktree2)

            # Step 5: Complete task 2 and verify cleanup
            cleanup_result2 = cleanup_task_worktree(task2["id"], push_commits=True)
            assert cleanup_result2 is True

            # Verify task 2 worktree is detached
            head_ref2 = run_git(
                ["rev-parse", "--abbrev-ref", "HEAD"],
                cwd=worktree2,
            ).stdout.strip()
            assert head_ref2 == "HEAD", f"Expected detached HEAD, got {head_ref2}"

            # Verify both worktrees still exist
            assert worktree1.exists()
            assert worktree2.exists()

            # Verify final state on origin has both commits
            remote_log_final = run_git(
                ["log", f"origin/{project_branch}", "--oneline"],
                cwd=project_repo,
            )
            assert "Task 1: Add feature" in remote_log_final.stdout
            assert "Task 2: Add more features" in remote_log_final.stdout

    def test_project_branch_not_defaulting_to_main(self, mock_config):
        """Verify project tasks don't default to main when on feature branch."""
        project_repo = mock_config["project_repo"]

        project_id = "PROJ-test002"
        project_branch = "project/no-main-default"

        task = {
            "id": "TASK-no-main",
            "file_path": f"/tmp/task-no-main.md",
            "title": "Feature Branch Task",
            "role": "implement",
            "priority": "P1",
            "project_id": project_id,
            "branch": "feature/test-branch",
        }

        def mock_get_project(proj_id):
            if proj_id == project_id:
                return {
                    "id": project_id,
                    "branch": project_branch,
                    "base_branch": "feature/test-branch",
                }
            return None

        with patch("orchestrator.queue_utils.get_project", side_effect=mock_get_project):
            worktree = create_task_worktree(task)

            # Verify the worktree is NOT based on main
            # Check merge-base with feature branch
            merge_base = run_git(
                ["merge-base", "HEAD", "origin/feature/test-branch"],
                cwd=worktree,
            ).stdout.strip()

            feature_head = run_git(
                ["rev-parse", "origin/feature/test-branch"],
                cwd=project_repo,
            ).stdout.strip()

            # merge-base should be the feature branch HEAD (or its ancestor)
            assert merge_base == feature_head, \
                f"Worktree should be based on feature branch, not main"

            # Cleanup
            cleanup_task_worktree(task["id"], push_commits=False)
