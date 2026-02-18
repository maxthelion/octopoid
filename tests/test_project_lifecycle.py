"""Integration test: mini 2-task project lifecycle.

Tests the full project lifecycle using a local bare repo as "origin":
- Project tasks sharing a branch
- Task 1: create worktree, verify base branch, make a commit, push manually
- Task 1 cleanup: worktree preserved with detached HEAD
- Task 2: create worktree, verify it sees task 1 commits
- Cleanup of test worktrees

Key API facts (as of current git_utils):
- create_task_worktree always creates detached HEAD worktrees
- cleanup_task_worktree only pushes if already on a named branch (which it never is
  after create_task_worktree); it always detaches HEAD and preserves the worktree
- Pushing commits is the responsibility of the agent (or test), not cleanup
"""

import subprocess
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.git_utils import (
    create_task_worktree,
    cleanup_task_worktree,
    run_git,
)


def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    """Run a bare git command (no abstraction layer)."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def lifecycle_env(tmp_path):
    """Set up a bare repo as 'origin' and a cloned main repo.

    Yields a dict:
        bare_repo:      Path to the bare repo (the remote)
        main_repo:      Path to the cloned repo (parent project)
        project_branch: Shared branch name for both tasks
        tasks_dir:      Runtime tasks directory (outside main_repo)
    """
    # ----------------------------------------------------------------
    # 1. Create a bare repo that will act as "origin"
    # ----------------------------------------------------------------
    bare_repo = tmp_path / "origin.git"
    _git(["init", "--bare", str(bare_repo)], cwd=tmp_path)

    # ----------------------------------------------------------------
    # 2. Clone to get the "main repo" (the parent project / octopoid root)
    # ----------------------------------------------------------------
    main_repo = tmp_path / "main_repo"
    _git(["clone", str(bare_repo), str(main_repo)], cwd=tmp_path)

    _git(["config", "user.email", "test@example.com"], cwd=main_repo)
    _git(["config", "user.name", "Test User"], cwd=main_repo)

    # Make an initial commit on main so the branch exists
    (main_repo / "README.md").write_text("# Project\n")
    _git(["add", "README.md"], cwd=main_repo)
    _git(["commit", "-m", "initial commit"], cwd=main_repo)
    _git(["push", "origin", "main"], cwd=main_repo)

    # ----------------------------------------------------------------
    # 3. Create a shared project branch and push it
    # ----------------------------------------------------------------
    project_branch = "feature/shared-project-branch"
    _git(["checkout", "-b", project_branch], cwd=main_repo)
    (main_repo / "project.md").write_text("# Shared project file\n")
    _git(["add", "project.md"], cwd=main_repo)
    _git(["commit", "-m", "feat: add project file"], cwd=main_repo)
    _git(["push", "origin", project_branch], cwd=main_repo)
    _git(["checkout", "main"], cwd=main_repo)  # Return to main

    # ----------------------------------------------------------------
    # 4. Tasks runtime dir (outside main_repo to avoid worktree conflicts)
    # ----------------------------------------------------------------
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()

    yield {
        "bare_repo": bare_repo,
        "main_repo": main_repo,
        "project_branch": project_branch,
        "tasks_dir": tasks_dir,
    }


def _patch_git_utils(main_repo: Path, tasks_dir: Path):
    """Return a context manager patching git_utils to use the test repos."""
    return patch.multiple(
        "orchestrator.git_utils",
        find_parent_project=lambda: main_repo,
        get_base_branch=lambda: "main",
        get_tasks_dir=lambda: tasks_dir,
    )


class TestMiniProjectLifecycle:
    """Integration test: two tasks sharing a project branch."""

    def test_two_task_lifecycle(self, lifecycle_env):
        """Full lifecycle: task1 commits visible in task2's worktree.

        Verifies:
        1. Worktrees are created as detached HEADs from the project branch
        2. Worktrees include files from the project branch (not just main)
        3. After manual push, task 2 sees task 1's commit
        4. cleanup_task_worktree preserves the worktree with detached HEAD
        """
        main_repo = lifecycle_env["main_repo"]
        tasks_dir = lifecycle_env["tasks_dir"]
        project_branch = lifecycle_env["project_branch"]

        task1 = {"id": "TASK-lc-0001", "branch": project_branch}
        task2 = {"id": "TASK-lc-0002", "branch": project_branch}

        with _patch_git_utils(main_repo, tasks_dir):
            # ----------------------------------------------------------
            # Task 1: create worktree
            # ----------------------------------------------------------
            worktree1 = create_task_worktree(task1)

            assert worktree1.exists(), "Task 1 worktree directory must exist"
            assert (worktree1 / ".git").exists(), "Task 1 worktree must have .git"

            # Worktree must be in detached HEAD state
            ref1 = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree1).stdout.strip()
            assert ref1 == "HEAD", f"Expected detached HEAD, got branch '{ref1}'"

            # Worktree must be based on project branch (not main)
            # project.md only exists on project_branch — not on main
            assert (worktree1 / "project.md").exists(), (
                "project.md must be present: worktree should be on project branch"
            )

            # Confirm ancestry: origin/project_branch is an ancestor of HEAD
            ancestor = _git(
                ["merge-base", "--is-ancestor", f"origin/{project_branch}", "HEAD"],
                cwd=worktree1,
                check=False,
            )
            assert ancestor.returncode == 0, (
                f"Worktree HEAD must descend from origin/{project_branch}"
            )

            # ----------------------------------------------------------
            # Task 1: configure identity and make a commit
            # ----------------------------------------------------------
            _git(["config", "user.email", "agent1@test.com"], cwd=worktree1)
            _git(["config", "user.name", "Agent One"], cwd=worktree1)

            (worktree1 / "task1_output.txt").write_text("Task 1 result\n")
            run_git(["add", "task1_output.txt"], cwd=worktree1)
            run_git(["commit", "-m", "feat: task 1 output"], cwd=worktree1)

            task1_commit = run_git(["rev-parse", "HEAD"], cwd=worktree1).stdout.strip()
            assert task1_commit, "task1_commit must be non-empty"

            # Push commit to origin under the shared project branch.
            # This is the agent's responsibility — cleanup_task_worktree does NOT
            # push commits from a detached HEAD worktree.
            run_git(["push", "origin", f"HEAD:{project_branch}"], cwd=worktree1)

            # ----------------------------------------------------------
            # Task 1 cleanup: detach HEAD, preserve worktree
            # ----------------------------------------------------------
            success = cleanup_task_worktree(task1["id"], push_commits=False)
            assert success is True, "cleanup_task_worktree should return True"

            # Worktree directory must still exist after cleanup
            assert worktree1.exists(), "Task 1 worktree must be preserved after cleanup"
            assert (worktree1 / ".git").exists(), "Task 1 .git must be preserved after cleanup"

            # HEAD must be detached after cleanup
            ref1_post = run_git(
                ["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree1
            ).stdout.strip()
            assert ref1_post == "HEAD", (
                f"Task 1 HEAD must be detached after cleanup, got '{ref1_post}'"
            )

            # ----------------------------------------------------------
            # Task 2: create worktree — must see task 1 commits
            # ----------------------------------------------------------
            worktree2 = create_task_worktree(task2)

            assert worktree2 != worktree1, "Task 2 worktree must differ from task 1"
            assert worktree2.exists(), "Task 2 worktree directory must exist"
            assert (worktree2 / ".git").exists(), "Task 2 worktree must have .git"

            # Detached HEAD
            ref2 = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree2).stdout.strip()
            assert ref2 == "HEAD", f"Task 2 worktree must be detached HEAD, got '{ref2}'"

            # Task 2 HEAD must include task 1's commit
            # (create_task_worktree fetches from origin before creating)
            task2_log = run_git(["rev-list", "HEAD"], cwd=worktree2).stdout
            assert task1_commit in task2_log, (
                "Task 2 worktree must include task 1's commit in its history"
            )

            # The file task 1 committed should be present
            assert (worktree2 / "task1_output.txt").exists(), (
                "task1_output.txt must be visible in task 2 worktree"
            )

            # project.md must still be present
            assert (worktree2 / "project.md").exists(), (
                "project.md must be present in task 2 worktree"
            )

            # Task 2 also descends from the project branch
            ancestor2 = _git(
                ["merge-base", "--is-ancestor", f"origin/{project_branch}", "HEAD"],
                cwd=worktree2,
                check=False,
            )
            assert ancestor2.returncode == 0, (
                f"Task 2 worktree HEAD must descend from origin/{project_branch}"
            )

    def test_cleanup_preserves_worktree_with_detached_head(self, lifecycle_env):
        """cleanup_task_worktree detaches HEAD and preserves the worktree directory."""
        main_repo = lifecycle_env["main_repo"]
        tasks_dir = lifecycle_env["tasks_dir"]
        project_branch = lifecycle_env["project_branch"]

        task = {"id": "TASK-cleanup-01", "branch": project_branch}

        with _patch_git_utils(main_repo, tasks_dir):
            worktree = create_task_worktree(task)
            assert worktree.exists()

            result = cleanup_task_worktree(task["id"], push_commits=False)
            assert result is True

            # Directory preserved
            assert worktree.exists(), "Worktree directory must survive cleanup"
            assert (worktree / ".git").exists(), ".git must survive cleanup"

            # HEAD is detached
            ref = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree).stdout.strip()
            assert ref == "HEAD", f"HEAD must be detached after cleanup, got '{ref}'"

    def test_worktree_base_branch_not_defaulting_to_main(self, lifecycle_env):
        """Task with project branch uses project branch, not main.

        project.md exists only on project_branch. Its presence in the worktree
        confirms the worktree was created from project_branch, not main.
        """
        main_repo = lifecycle_env["main_repo"]
        tasks_dir = lifecycle_env["tasks_dir"]
        project_branch = lifecycle_env["project_branch"]

        task = {"id": "TASK-branch-check", "branch": project_branch}

        with _patch_git_utils(main_repo, tasks_dir):
            worktree = create_task_worktree(task)

            assert (worktree / "project.md").exists(), (
                "project.md should be present — worktree must be on project branch, not main"
            )

            # origin/project_branch is ancestor of worktree HEAD
            ancestor = _git(
                ["merge-base", "--is-ancestor", f"origin/{project_branch}", "HEAD"],
                cwd=worktree,
                check=False,
            )
            assert ancestor.returncode == 0, (
                "Worktree HEAD must descend from project branch, not just main"
            )
