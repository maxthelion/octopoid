"""Unit tests for create_project() in orchestrator/projects.py.

Verifies that:
- create_project() auto-generates a branch from the project ID when none is provided
- create_project() uses the explicitly provided branch when given
- The generated branch follows the feature/{short_id} pattern
"""

import pytest
from unittest.mock import patch, MagicMock


class TestCreateProjectBranchAutoGeneration:
    """create_project() auto-generates a branch when none is provided."""

    def test_no_branch_generates_feature_branch(self, mock_sdk_for_unit_tests):
        """When branch is not provided, a feature branch is auto-generated."""
        mock_sdk_for_unit_tests.projects.create.return_value = {
            "id": "PROJ-abc12345",
            "title": "My Project",
            "branch": "feature/abc12345",
            "status": "draft",
        }

        from orchestrator.projects import create_project

        project = create_project(
            title="My Project",
            description="A test project",
        )

        # SDK must have been called with a non-null branch
        call_kwargs = mock_sdk_for_unit_tests.projects.create.call_args[1]
        assert call_kwargs["branch"] is not None, (
            "create_project() must pass a branch to the SDK when none is provided"
        )
        assert call_kwargs["branch"].startswith("feature/"), (
            f"Auto-generated branch must start with 'feature/', got: {call_kwargs['branch']!r}"
        )

    def test_no_branch_uses_short_project_id(self, mock_sdk_for_unit_tests):
        """The auto-generated branch uses the short project ID suffix."""
        captured_kwargs = {}

        def capture_create(**kwargs):
            captured_kwargs.update(kwargs)
            return {"id": kwargs["id"], "title": kwargs["title"], "branch": kwargs["branch"], "status": "draft"}

        mock_sdk_for_unit_tests.projects.create.side_effect = capture_create

        from orchestrator.projects import create_project

        project = create_project(
            title="Test",
            description="desc",
        )

        project_id = captured_kwargs["id"]  # e.g. PROJ-abc12345
        branch = captured_kwargs["branch"]   # e.g. feature/abc12345

        short_id = project_id.replace("PROJ-", "")[:8]
        assert branch == f"feature/{short_id}", (
            f"Branch must be feature/{{short_id}}, expected 'feature/{short_id}', got '{branch}'"
        )

    def test_explicit_branch_is_used_as_is(self, mock_sdk_for_unit_tests):
        """When branch is explicitly provided, it is used without modification."""
        explicit_branch = "my-custom-branch"

        mock_sdk_for_unit_tests.projects.create.return_value = {
            "id": "PROJ-xyz00001",
            "title": "Custom Branch Project",
            "branch": explicit_branch,
            "status": "draft",
        }

        from orchestrator.projects import create_project

        create_project(
            title="Custom Branch Project",
            description="Uses explicit branch",
            branch=explicit_branch,
        )

        call_kwargs = mock_sdk_for_unit_tests.projects.create.call_args[1]
        assert call_kwargs["branch"] == explicit_branch, (
            f"Explicit branch must be passed unchanged, expected '{explicit_branch}', "
            f"got '{call_kwargs['branch']}'"
        )

    def test_generated_branch_always_non_empty(self, mock_sdk_for_unit_tests):
        """Multiple calls to create_project() without branch all produce non-empty branches."""
        mock_sdk_for_unit_tests.projects.create.return_value = {
            "id": "PROJ-test0001",
            "title": "Test",
            "branch": "feature/test0001",
            "status": "draft",
        }

        from orchestrator.projects import create_project

        for _ in range(5):
            create_project(title="Test", description="desc")
            call_kwargs = mock_sdk_for_unit_tests.projects.create.call_args[1]
            assert call_kwargs["branch"], "Branch must always be non-empty"
            assert call_kwargs["branch"].startswith("feature/"), (
                "Branch must always start with 'feature/'"
            )
