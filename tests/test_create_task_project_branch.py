"""Tests for create_task() project branch inheritance.

Verifies that:
- create_task(project_id=X) without branch= inherits the project's branch
- create_task(project_id=X, branch=Y) uses the explicit branch (override)
"""

from pathlib import Path
from unittest.mock import patch

import pytest


class TestCreateTaskProjectBranchInheritance:
    """create_task() inherits branch from project when not explicitly provided."""

    def test_task_inherits_project_branch(
        self, mock_orchestrator_dir, mock_sdk_for_unit_tests
    ):
        """Task created with project_id but no branch inherits the project's branch."""
        project_branch = "feature/my-project"
        project_id = "PROJ-abc12345"

        mock_sdk_for_unit_tests.projects.get.return_value = {
            "id": project_id,
            "title": "My Project",
            "branch": project_branch,
            "status": "active",
        }

        tasks_dir = mock_orchestrator_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        with patch(
            "orchestrator.tasks.get_tasks_file_dir",
            return_value=tasks_dir,
        ):
            from orchestrator.tasks import create_task

            task_path = create_task(
                title="Child task without explicit branch",
                role="implement",
                context="Inherits branch from project",
                acceptance_criteria=["Do the thing"],
                project_id=project_id,
            )

        # Task file must exist and contain the project branch
        assert task_path.exists(), "Task file must be created"
        content = task_path.read_text()
        assert f"BRANCH: {project_branch}" in content, (
            f"Task must inherit project branch '{project_branch}'"
        )

        # SDK create must have been called with the project branch
        mock_sdk_for_unit_tests.projects.get.assert_called_once_with(project_id)
        call_kwargs = mock_sdk_for_unit_tests.tasks.create.call_args[1]
        assert call_kwargs["branch"] == project_branch, (
            f"SDK create must receive branch='{project_branch}'"
        )

    def test_explicit_branch_overrides_project_branch(
        self, mock_orchestrator_dir, mock_sdk_for_unit_tests
    ):
        """Explicit branch= overrides the project's branch."""
        project_branch = "feature/my-project"
        explicit_branch = "other-branch"
        project_id = "PROJ-abc12345"

        mock_sdk_for_unit_tests.projects.get.return_value = {
            "id": project_id,
            "title": "My Project",
            "branch": project_branch,
            "status": "active",
        }

        tasks_dir = mock_orchestrator_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        with patch(
            "orchestrator.tasks.get_tasks_file_dir",
            return_value=tasks_dir,
        ):
            from orchestrator.tasks import create_task

            task_path = create_task(
                title="Child task with explicit branch",
                role="implement",
                context="Uses explicit branch, ignores project branch",
                acceptance_criteria=["Do the other thing"],
                project_id=project_id,
                branch=explicit_branch,
            )

        assert task_path.exists(), "Task file must be created"
        content = task_path.read_text()
        assert f"BRANCH: {explicit_branch}" in content, (
            f"Task must use explicit branch '{explicit_branch}'"
        )
        assert f"BRANCH: {project_branch}" not in content, (
            "Task must NOT use project branch when explicit branch is provided"
        )

        # projects.get must NOT be called — branch was provided explicitly
        mock_sdk_for_unit_tests.projects.get.assert_not_called()

        call_kwargs = mock_sdk_for_unit_tests.tasks.create.call_args[1]
        assert call_kwargs["branch"] == explicit_branch

    def test_task_without_project_id_uses_base_branch(
        self, mock_orchestrator_dir, mock_sdk_for_unit_tests
    ):
        """Task without project_id falls back to get_base_branch()."""
        tasks_dir = mock_orchestrator_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        with patch(
            "orchestrator.tasks.get_tasks_file_dir",
            return_value=tasks_dir,
        ):
            with patch(
                "orchestrator.tasks.get_base_branch", return_value="main"
            ):
                from orchestrator.tasks import create_task

                task_path = create_task(
                    title="Standalone task no project",
                    role="implement",
                    context="No project context",
                    acceptance_criteria=["Done"],
                )

        assert task_path.exists()
        content = task_path.read_text()
        assert "BRANCH: main" in content


class TestCreateTaskFlowParameter:
    """create_task() accepts an optional flow= parameter."""

    def test_explicit_flow_is_passed_to_sdk(
        self, mock_orchestrator_dir, mock_sdk_for_unit_tests
    ):
        """Explicit flow= is forwarded to sdk.tasks.create()."""
        tasks_dir = mock_orchestrator_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        with patch(
            "orchestrator.tasks.get_tasks_file_dir",
            return_value=tasks_dir,
        ):
            with patch(
                "orchestrator.tasks.get_base_branch", return_value="main"
            ):
                from orchestrator.tasks import create_task

                create_task(
                    title="Fast task",
                    role="implement",
                    context="Uses fast flow",
                    acceptance_criteria=["Done"],
                    flow="fast",
                )

        call_kwargs = mock_sdk_for_unit_tests.tasks.create.call_args[1]
        assert call_kwargs["flow"] == "fast"

    def test_default_flow_without_project(
        self, mock_orchestrator_dir, mock_sdk_for_unit_tests
    ):
        """Without flow= or project_id, flow defaults to 'default'."""
        tasks_dir = mock_orchestrator_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        with patch(
            "orchestrator.tasks.get_tasks_file_dir",
            return_value=tasks_dir,
        ):
            with patch(
                "orchestrator.tasks.get_base_branch", return_value="main"
            ):
                from orchestrator.tasks import create_task

                create_task(
                    title="Default flow task",
                    role="implement",
                    context="No explicit flow",
                    acceptance_criteria=["Done"],
                )

        call_kwargs = mock_sdk_for_unit_tests.tasks.create.call_args[1]
        assert call_kwargs["flow"] == "default"

    def test_default_flow_with_project_id(
        self, mock_orchestrator_dir, mock_sdk_for_unit_tests
    ):
        """Without flow= but with project_id, flow defaults to 'project'."""
        project_id = "PROJ-abc12345"
        mock_sdk_for_unit_tests.projects.get.return_value = {
            "id": project_id,
            "title": "My Project",
            "branch": "main",
            "status": "active",
        }

        tasks_dir = mock_orchestrator_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        with patch(
            "orchestrator.tasks.get_tasks_file_dir",
            return_value=tasks_dir,
        ):
            from orchestrator.tasks import create_task

            create_task(
                title="Project task",
                role="implement",
                context="Has project, no explicit flow",
                acceptance_criteria=["Done"],
                project_id=project_id,
            )

        call_kwargs = mock_sdk_for_unit_tests.tasks.create.call_args[1]
        assert call_kwargs["flow"] == "project"

    def test_explicit_flow_overrides_project_default(
        self, mock_orchestrator_dir, mock_sdk_for_unit_tests
    ):
        """Explicit flow= overrides the 'project' default even when project_id is set."""
        project_id = "PROJ-abc12345"
        mock_sdk_for_unit_tests.projects.get.return_value = {
            "id": project_id,
            "title": "My Project",
            "branch": "main",
            "status": "active",
        }

        tasks_dir = mock_orchestrator_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        with patch(
            "orchestrator.tasks.get_tasks_file_dir",
            return_value=tasks_dir,
        ):
            from orchestrator.tasks import create_task

            create_task(
                title="Project task with custom flow",
                role="implement",
                context="Has project_id but overrides flow",
                acceptance_criteria=["Done"],
                project_id=project_id,
                flow="fast",
            )

        call_kwargs = mock_sdk_for_unit_tests.tasks.create.call_args[1]
        assert call_kwargs["flow"] == "fast"

    def test_project_without_branch_falls_back_to_base_branch(
        self, mock_orchestrator_dir, mock_sdk_for_unit_tests
    ):
        """If the project has no branch set, fall back to get_base_branch()."""
        project_id = "PROJ-nobranch"

        mock_sdk_for_unit_tests.projects.get.return_value = {
            "id": project_id,
            "title": "Project with no branch",
            "branch": None,
            "status": "draft",
        }

        tasks_dir = mock_orchestrator_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        with patch(
            "orchestrator.tasks.get_tasks_file_dir",
            return_value=tasks_dir,
        ):
            with patch(
                "orchestrator.tasks.get_base_branch", return_value="main"
            ):
                from orchestrator.tasks import create_task

                task_path = create_task(
                    title="Task under branchless project",
                    role="implement",
                    context="Project has no branch",
                    acceptance_criteria=["Done"],
                    project_id=project_id,
                )

        assert task_path.exists()
        content = task_path.read_text()
        assert "BRANCH: main" in content
