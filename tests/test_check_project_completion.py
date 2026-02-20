"""Unit tests for check_project_completion() in scheduler.py.

These tests mock the SDK and subprocess calls since:
- The function talks to an external server (SDK)
- The function runs gh CLI commands
"""

from unittest.mock import MagicMock, patch, call
import pytest

from orchestrator.scheduler import check_project_completion


def _make_sdk(projects=None, tasks_by_project=None):
    """Build a mock SDK with projects and tasks configured."""
    sdk = MagicMock()
    sdk.projects.list.return_value = projects or []
    if tasks_by_project:
        sdk.projects.get_tasks.side_effect = lambda pid: tasks_by_project.get(pid, [])
    else:
        sdk.projects.get_tasks.return_value = []
    sdk.projects.update.return_value = {}
    return sdk


class TestCheckProjectCompletion:
    """Tests for the check_project_completion housekeeping job."""

    def test_no_active_projects_does_nothing(self):
        """When there are no active projects, nothing happens."""
        sdk = _make_sdk(projects=[])

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
            check_project_completion()

        sdk.projects.get_tasks.assert_not_called()
        sdk.projects.update.assert_not_called()

    def test_project_with_no_tasks_is_skipped(self):
        """A project that has no tasks should not be promoted."""
        sdk = _make_sdk(
            projects=[{"id": "PROJ-abc", "status": "active", "branch": "feature/proj-abc", "title": "My Project"}],
            tasks_by_project={"PROJ-abc": []},
        )

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
            check_project_completion()

        sdk.projects.update.assert_not_called()

    def test_project_with_incomplete_tasks_is_skipped(self):
        """A project where some tasks are not done should not be promoted."""
        sdk = _make_sdk(
            projects=[{"id": "PROJ-abc", "status": "active", "branch": "feature/proj-abc", "title": "My Project"}],
            tasks_by_project={
                "PROJ-abc": [
                    {"id": "TASK-1", "queue": "done"},
                    {"id": "TASK-2", "queue": "incoming"},
                ]
            },
        )

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
            check_project_completion()

        sdk.projects.update.assert_not_called()

    def test_project_with_all_done_tasks_creates_pr_and_updates_status(self):
        """When all tasks are done, a PR is created and project moved to review."""
        project_id = "PROJ-abc"
        project_branch = "feature/proj-abc"

        sdk = _make_sdk(
            projects=[{"id": project_id, "status": "active", "branch": project_branch, "title": "My Project"}],
            tasks_by_project={
                project_id: [
                    {"id": "TASK-1", "queue": "done"},
                    {"id": "TASK-2", "queue": "done"},
                ]
            },
        )

        # gh pr view returns non-zero (no existing PR)
        pr_view_result = MagicMock()
        pr_view_result.returncode = 1
        pr_view_result.stdout = ""
        pr_view_result.stderr = ""

        # gh pr create returns the URL
        pr_create_result = MagicMock()
        pr_create_result.returncode = 0
        pr_create_result.stdout = "https://github.com/owner/repo/pull/42\n"
        pr_create_result.stderr = ""

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.scheduler.get_base_branch", return_value="main"),
            patch("orchestrator.scheduler.find_parent_project", return_value="/fake/project"),
            patch("orchestrator.scheduler.subprocess.run", side_effect=[pr_view_result, pr_create_result]) as mock_run,
        ):
            check_project_completion()

        # PR should have been created with correct args
        create_call = mock_run.call_args_list[1]
        cmd = create_call[0][0]
        assert "gh" in cmd
        assert "pr" in cmd
        assert "create" in cmd
        assert "--base" in cmd
        assert "main" in cmd
        assert "--head" in cmd
        assert project_branch in cmd

        # Project should be updated with review status and PR info
        sdk.projects.update.assert_called_once_with(
            project_id,
            status="review",
            pr_url="https://github.com/owner/repo/pull/42",
            pr_number=42,
        )

    def test_existing_pr_is_reused(self):
        """When a PR already exists for the project branch, it's reused."""
        project_id = "PROJ-xyz"
        project_branch = "feature/proj-xyz"

        sdk = _make_sdk(
            projects=[{"id": project_id, "status": "active", "branch": project_branch, "title": "Existing PR Project"}],
            tasks_by_project={
                project_id: [{"id": "TASK-1", "queue": "done"}],
            },
        )

        # gh pr view returns existing PR info
        pr_view_result = MagicMock()
        pr_view_result.returncode = 0
        pr_view_result.stdout = "https://github.com/owner/repo/pull/99 99\n"
        pr_view_result.stderr = ""

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.scheduler.get_base_branch", return_value="main"),
            patch("orchestrator.scheduler.find_parent_project", return_value="/fake/project"),
            patch("orchestrator.scheduler.subprocess.run", return_value=pr_view_result) as mock_run,
        ):
            check_project_completion()

        # Only gh pr view should be called (not create)
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert "view" in cmd

        # Project should still be updated to review with existing PR info
        sdk.projects.update.assert_called_once_with(
            project_id,
            status="review",
            pr_url="https://github.com/owner/repo/pull/99",
            pr_number=99,
        )

    def test_skips_projects_already_in_review_status(self):
        """Projects with status 'review' returned by the API are skipped."""
        sdk = _make_sdk(
            projects=[{"id": "PROJ-done", "status": "review", "branch": "feature/done", "title": "Done"}],
        )

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
            check_project_completion()

        sdk.projects.get_tasks.assert_not_called()
        sdk.projects.update.assert_not_called()

    def test_skips_projects_already_completed(self):
        """Projects with status 'completed' returned by the API are skipped."""
        sdk = _make_sdk(
            projects=[{"id": "PROJ-comp", "status": "completed", "branch": "feature/comp", "title": "Completed"}],
        )

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
            check_project_completion()

        sdk.projects.get_tasks.assert_not_called()
        sdk.projects.update.assert_not_called()

    def test_project_without_branch_is_skipped(self):
        """A project with no branch field should be skipped gracefully."""
        project_id = "PROJ-nobranch"
        sdk = _make_sdk(
            projects=[{"id": project_id, "status": "active", "branch": None, "title": "No Branch"}],
            tasks_by_project={project_id: [{"id": "TASK-1", "queue": "done"}]},
        )

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
            check_project_completion()

        sdk.projects.update.assert_not_called()

    def test_sdk_error_does_not_crash(self):
        """An SDK error should be caught and not propagate."""
        with patch(
            "orchestrator.scheduler.queue_utils.get_sdk",
            side_effect=Exception("connection refused"),
        ):
            # Should not raise
            check_project_completion()

    def test_pr_creation_failure_skips_project(self):
        """If gh pr create fails, the project is not updated."""
        project_id = "PROJ-prfail"
        project_branch = "feature/pr-fail"

        sdk = _make_sdk(
            projects=[{"id": project_id, "status": "active", "branch": project_branch, "title": "PR Fail"}],
            tasks_by_project={project_id: [{"id": "TASK-1", "queue": "done"}]},
        )

        pr_view_result = MagicMock(returncode=1, stdout="", stderr="")
        pr_create_result = MagicMock(returncode=1, stdout="", stderr="some gh error")

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.scheduler.get_base_branch", return_value="main"),
            patch("orchestrator.scheduler.find_parent_project", return_value="/fake/project"),
            patch("orchestrator.scheduler.subprocess.run", side_effect=[pr_view_result, pr_create_result]),
        ):
            check_project_completion()

        sdk.projects.update.assert_not_called()

    def test_multiple_projects_processed_independently(self):
        """Multiple active projects are each checked independently."""
        proj1_id = "PROJ-001"
        proj2_id = "PROJ-002"

        sdk = _make_sdk(
            projects=[
                {"id": proj1_id, "status": "active", "branch": "feature/001", "title": "Project 1"},
                {"id": proj2_id, "status": "active", "branch": "feature/002", "title": "Project 2"},
            ],
            tasks_by_project={
                proj1_id: [{"id": "TASK-1", "queue": "done"}],
                proj2_id: [{"id": "TASK-2", "queue": "incoming"}],  # not done
            },
        )

        pr_view_result = MagicMock(returncode=1, stdout="", stderr="")
        pr_create_result = MagicMock(
            returncode=0,
            stdout="https://github.com/owner/repo/pull/10\n",
            stderr="",
        )

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.scheduler.get_base_branch", return_value="main"),
            patch("orchestrator.scheduler.find_parent_project", return_value="/fake/project"),
            patch("orchestrator.scheduler.subprocess.run", side_effect=[pr_view_result, pr_create_result]),
        ):
            check_project_completion()

        # Only proj1 should be updated (proj2 has tasks not done)
        sdk.projects.update.assert_called_once_with(
            proj1_id,
            status="review",
            pr_url="https://github.com/owner/repo/pull/10",
            pr_number=10,
        )
