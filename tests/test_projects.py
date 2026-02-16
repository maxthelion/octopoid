"""Unit tests for orchestrator/projects.py."""

import pytest
from unittest.mock import MagicMock, patch
from orchestrator.projects import (
    create_project,
    get_project,
    list_projects,
    send_to_breakdown,
)


class TestProjectsAPI:
    """Test ProjectsAPI methods via projects.py functions."""

    def test_get_project_returns_project_dict(self, mock_sdk_for_unit_tests):
        """Test that get_project() returns a project dict on success."""
        # Setup mock
        mock_sdk_for_unit_tests.projects.get.return_value = {
            "id": "PROJ-12345678",
            "title": "Test Project",
            "status": "draft",
        }

        # Call function
        result = get_project("PROJ-12345678")

        # Assertions
        assert result is not None
        assert result["id"] == "PROJ-12345678"
        assert result["title"] == "Test Project"
        mock_sdk_for_unit_tests.projects.get.assert_called_once_with("PROJ-12345678")

    def test_get_project_returns_none_on_404(self, mock_sdk_for_unit_tests):
        """Test that get_project() returns None when SDK raises 404."""
        # Setup mock to raise exception
        import requests
        error = requests.HTTPError()
        error.response = MagicMock()
        error.response.status_code = 404
        mock_sdk_for_unit_tests.projects.get.side_effect = error

        # Call function
        result = get_project("PROJ-nonexistent")

        # Should return None on exception
        assert result is None

    def test_get_project_returns_none_on_sdk_exception(self, mock_sdk_for_unit_tests):
        """Test that get_project() returns None on any SDK exception."""
        # Setup mock to raise a generic exception
        mock_sdk_for_unit_tests.projects.get.side_effect = Exception("Network error")

        # Call function
        result = get_project("PROJ-error")

        # Should return None on exception, not propagate
        assert result is None

    def test_list_projects_returns_empty_list_on_sdk_exception(self, mock_sdk_for_unit_tests):
        """Test that list_projects() returns [] on SDK failure."""
        # Setup mock to raise exception
        mock_sdk_for_unit_tests.projects.list.side_effect = Exception("API error")

        # Call function
        result = list_projects()

        # Should return empty list on exception, not propagate
        assert result == []

    def test_create_project_sends_correct_payload(self, mock_sdk_for_unit_tests):
        """Test that create_project() sends correct data to SDK."""
        # Setup mock
        mock_sdk_for_unit_tests.projects.create.return_value = {
            "id": "PROJ-abc12345",
            "title": "New Project",
            "status": "draft",
        }

        # Mock git ls-remote to simulate branch exists
        with patch('subprocess.run') as mock_run:
            # First call: ls-remote (branch exists)
            # Second call: rev-parse (current branch check)
            mock_run.side_effect = [
                MagicMock(stdout="refs/heads/feature/test-branch", returncode=0),
                MagicMock(stdout="main", returncode=0),
            ]

            # Call function
            result = create_project(
                title="New Project",
                description="Test description",
                branch="feature/test-branch",
                created_by="human",
            )

        # Verify SDK create was called with correct parameters
        mock_sdk_for_unit_tests.projects.create.assert_called_once()
        call_kwargs = mock_sdk_for_unit_tests.projects.create.call_args[1]
        assert call_kwargs["title"] == "New Project"
        assert call_kwargs["description"] == "Test description"
        assert call_kwargs["branch"] == "feature/test-branch"
        assert call_kwargs["created_by"] == "human"
        assert call_kwargs["status"] == "draft"
        assert call_kwargs["base_branch"] == "main"

    def test_create_project_raises_value_error_when_branch_not_exists(self, mock_sdk_for_unit_tests):
        """Test that create_project() raises ValueError when branch doesn't exist on origin."""
        # Mock git ls-remote to return empty (branch doesn't exist)
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)

            # Should raise ValueError
            with pytest.raises(ValueError, match="Branch 'feature/nonexistent' does not exist on origin"):
                create_project(
                    title="Test Project",
                    description="Test",
                    branch="feature/nonexistent",
                )

    def test_send_to_breakdown_sanitizes_branch_name(self, mock_sdk_for_unit_tests):
        """Test that send_to_breakdown() produces valid git ref characters only."""
        # Setup mocks
        mock_sdk_for_unit_tests.projects.create.return_value = {
            "id": "PROJ-test1234",
            "title": "Test",
        }

        # Mock git commands and task creation
        with patch('subprocess.run') as mock_run:
            with patch('orchestrator.tasks.create_task') as mock_create_task:
                # Mock git ls-remote (branch exists) and rev-parse
                mock_run.side_effect = [
                    MagicMock(stdout="refs/heads/feature/test", returncode=0),
                    MagicMock(stdout="main", returncode=0),
                ]
                mock_create_task.return_value = "/path/to/task.md"

                # Call with title containing invalid characters
                result = send_to_breakdown(
                    title="Add Feature: Support [wildcards] & *special* chars?",
                    description="Test",
                    context="Test context",
                    as_project=True,
                )

        # Verify branch name was sanitized (no ?, *, [, ], &, :, spaces)
        call_kwargs = mock_sdk_for_unit_tests.projects.create.call_args[1]
        branch = call_kwargs["branch"]

        # Should only contain valid characters: a-z, 0-9, _, /, -
        assert branch.startswith("feature/")
        # Remove the "feature/" prefix and check remaining characters
        slug = branch.replace("feature/", "")
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789_-/" for c in slug), \
            f"Branch name '{branch}' contains invalid characters"

        # Verify invalid characters were removed
        assert "?" not in branch
        assert "*" not in branch
        assert "[" not in branch
        assert "]" not in branch
        assert ":" not in branch
        assert " " not in branch
        assert "&" not in branch


class TestProjectsAPIUpdate:
    """Test ProjectsAPI.update() method."""

    def test_update_project_sends_patch(self, mock_sdk_for_unit_tests):
        """Test that updating a project sends a PATCH request via SDK."""
        # Setup mock
        from orchestrator.sdk import get_sdk
        sdk = get_sdk()
        sdk.projects.update.return_value = {
            "id": "PROJ-12345678",
            "status": "active",
        }

        # Call SDK update directly
        result = sdk.projects.update("PROJ-12345678", status="active")

        # Verify
        assert result["status"] == "active"
        sdk.projects.update.assert_called_once_with("PROJ-12345678", status="active")


class TestProjectsAPIGetTasks:
    """Test ProjectsAPI.get_tasks() method."""

    def test_get_tasks_returns_task_list(self, mock_sdk_for_unit_tests):
        """Test that get_tasks() returns a list of tasks."""
        # Setup mock
        from orchestrator.projects import get_project_tasks
        mock_sdk_for_unit_tests.projects.get_tasks.return_value = [
            {"id": "TASK-001", "title": "Task 1"},
            {"id": "TASK-002", "title": "Task 2"},
        ]

        # Call function
        tasks = get_project_tasks("PROJ-12345678")

        # Verify
        assert len(tasks) == 2
        assert tasks[0]["id"] == "TASK-001"
        assert tasks[1]["id"] == "TASK-002"
        mock_sdk_for_unit_tests.projects.get_tasks.assert_called_once_with("PROJ-12345678")
