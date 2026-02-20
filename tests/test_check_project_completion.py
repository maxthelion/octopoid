"""Unit tests for check_project_completion() and related flow functions in scheduler.py.

The new implementation routes project completion through the flow engine:
- check_project_completion() calls _execute_project_flow_transition()
- _execute_project_flow_transition() loads the project flow, evaluates conditions,
  executes steps via execute_steps(), and updates project status
- approve_project_via_flow() handles the provisional -> done transition

Patch targets:
- Flow loading: orchestrator.flow.load_flow (local import inside functions)
- Step execution: orchestrator.steps.execute_steps (local import inside functions)
- Parent project dir in scheduler: orchestrator.scheduler.find_parent_project
- Parent project dir in projects: orchestrator.config.find_parent_project
- SDK in projects: orchestrator.projects.get_sdk
"""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


def _make_sdk(projects=None, tasks_by_project=None, project_by_id=None):
    """Build a mock SDK with projects and tasks configured."""
    sdk = MagicMock()
    sdk.projects.list.return_value = projects or []
    if tasks_by_project:
        sdk.projects.get_tasks.side_effect = lambda pid: tasks_by_project.get(pid, [])
    else:
        sdk.projects.get_tasks.return_value = []
    sdk.projects.update.return_value = {}
    if project_by_id:
        sdk.projects.get.side_effect = lambda pid: project_by_id.get(pid)
    return sdk


def _make_flow(from_state: str, to_state: str, runs: list | None = None, conditions: list | None = None):
    """Build a minimal mock Flow with a single transition."""
    transition = MagicMock()
    transition.from_state = from_state
    transition.to_state = to_state
    transition.runs = runs or []
    transition.conditions = conditions or []

    flow = MagicMock()
    flow.get_transitions_from.side_effect = (
        lambda state: [transition] if state == from_state else []
    )
    return flow


class TestCheckProjectCompletion:
    """Tests for the check_project_completion housekeeping job."""

    def test_no_active_projects_does_nothing(self):
        sdk = _make_sdk(projects=[])

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
            from orchestrator.scheduler import check_project_completion
            check_project_completion()

        sdk.projects.get_tasks.assert_not_called()
        sdk.projects.update.assert_not_called()

    def test_project_with_no_tasks_is_skipped(self):
        sdk = _make_sdk(
            projects=[{"id": "PROJ-abc", "status": "active", "branch": "feature/proj-abc", "title": "My Project"}],
            tasks_by_project={"PROJ-abc": []},
        )

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
            from orchestrator.scheduler import check_project_completion
            check_project_completion()

        sdk.projects.update.assert_not_called()

    def test_project_with_incomplete_tasks_is_skipped(self):
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
            from orchestrator.scheduler import check_project_completion
            check_project_completion()

        sdk.projects.update.assert_not_called()

    def test_all_done_tasks_triggers_flow_transition(self):
        """When all tasks are done, the flow's children_complete -> provisional transition runs."""
        project_id = "PROJ-abc"
        project_branch = "feature/proj-abc"
        project = {"id": project_id, "status": "active", "branch": project_branch, "title": "My Project"}
        updated_project = {**project, "pr_url": "https://github.com/owner/repo/pull/42", "pr_number": 42}

        sdk = _make_sdk(
            projects=[project],
            tasks_by_project={
                project_id: [
                    {"id": "TASK-1", "queue": "done"},
                    {"id": "TASK-2", "queue": "done"},
                ]
            },
            project_by_id={project_id: updated_project},
        )

        mock_flow = _make_flow("children_complete", "provisional", runs=["create_project_pr"])

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.scheduler.find_parent_project", return_value=Path("/fake/project")),
            patch("orchestrator.flow.load_flow", return_value=mock_flow),
            patch("orchestrator.steps.execute_steps") as mock_execute_steps,
        ):
            from orchestrator.scheduler import check_project_completion
            check_project_completion()

        # Flow steps should have been executed with the project dict
        mock_execute_steps.assert_called_once_with(
            ["create_project_pr"], project, {}, Path("/fake/project")
        )
        # Project status should be updated to the flow's to_state ("provisional")
        sdk.projects.update.assert_called_once_with(project_id, status="provisional")

    def test_skips_projects_already_in_provisional_status(self):
        sdk = _make_sdk(
            projects=[{"id": "PROJ-prov", "status": "provisional", "branch": "feature/prov", "title": "In Review"}],
        )

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
            from orchestrator.scheduler import check_project_completion
            check_project_completion()

        sdk.projects.get_tasks.assert_not_called()
        sdk.projects.update.assert_not_called()

    def test_skips_projects_already_in_review_status(self):
        sdk = _make_sdk(
            projects=[{"id": "PROJ-done", "status": "review", "branch": "feature/done", "title": "Done"}],
        )

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
            from orchestrator.scheduler import check_project_completion
            check_project_completion()

        sdk.projects.get_tasks.assert_not_called()
        sdk.projects.update.assert_not_called()

    def test_skips_projects_already_completed(self):
        sdk = _make_sdk(
            projects=[{"id": "PROJ-comp", "status": "completed", "branch": "feature/comp", "title": "Completed"}],
        )

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
            from orchestrator.scheduler import check_project_completion
            check_project_completion()

        sdk.projects.get_tasks.assert_not_called()
        sdk.projects.update.assert_not_called()

    def test_project_without_branch_is_skipped(self):
        project_id = "PROJ-nobranch"
        sdk = _make_sdk(
            projects=[{"id": project_id, "status": "active", "branch": None, "title": "No Branch"}],
            tasks_by_project={project_id: [{"id": "TASK-1", "queue": "done"}]},
        )

        with patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk):
            from orchestrator.scheduler import check_project_completion
            check_project_completion()

        sdk.projects.update.assert_not_called()

    def test_sdk_error_does_not_crash(self):
        with patch(
            "orchestrator.scheduler.queue_utils.get_sdk",
            side_effect=Exception("connection refused"),
        ):
            from orchestrator.scheduler import check_project_completion
            check_project_completion()  # Should not raise

    def test_flow_not_found_does_not_crash(self):
        """If the project flow file doesn't exist, skip gracefully."""
        project_id = "PROJ-noflow"
        project = {"id": project_id, "status": "active", "branch": "feature/noflow", "title": "No Flow"}

        sdk = _make_sdk(
            projects=[project],
            tasks_by_project={project_id: [{"id": "TASK-1", "queue": "done"}]},
        )

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.scheduler.find_parent_project", return_value=Path("/fake")),
            patch("orchestrator.flow.load_flow", side_effect=FileNotFoundError("not found")),
        ):
            from orchestrator.scheduler import check_project_completion
            check_project_completion()

        sdk.projects.update.assert_not_called()

    def test_script_condition_failure_blocks_transition(self):
        """If a script condition fails, the flow transition is not executed."""
        project_id = "PROJ-testfail"
        project = {"id": project_id, "status": "active", "branch": "feature/testfail", "title": "Test Fail"}

        sdk = _make_sdk(
            projects=[project],
            tasks_by_project={project_id: [{"id": "TASK-1", "queue": "done"}]},
        )

        mock_condition = MagicMock()
        mock_condition.name = "all_tests_pass"
        mock_condition.type = "script"
        mock_condition.script = "run-tests"

        mock_flow = _make_flow(
            "children_complete", "provisional",
            runs=["create_project_pr"],
            conditions=[mock_condition],
        )

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.scheduler.find_parent_project", return_value=Path("/fake")),
            patch("orchestrator.flow.load_flow", return_value=mock_flow),
            patch("orchestrator.scheduler._evaluate_project_script_condition", return_value=False),
            patch("orchestrator.steps.execute_steps") as mock_execute_steps,
        ):
            from orchestrator.scheduler import check_project_completion
            check_project_completion()

        mock_execute_steps.assert_not_called()
        sdk.projects.update.assert_not_called()

    def test_script_condition_passing_allows_transition(self):
        """If script conditions all pass, the flow transition executes."""
        project_id = "PROJ-testpass"
        project = {"id": project_id, "status": "active", "branch": "feature/testpass", "title": "Test Pass"}

        sdk = _make_sdk(
            projects=[project],
            tasks_by_project={project_id: [{"id": "TASK-1", "queue": "done"}]},
            project_by_id={project_id: project},
        )

        mock_condition = MagicMock()
        mock_condition.name = "all_tests_pass"
        mock_condition.type = "script"
        mock_condition.script = "run-tests"

        mock_flow = _make_flow(
            "children_complete", "provisional",
            runs=["create_project_pr"],
            conditions=[mock_condition],
        )

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.scheduler.find_parent_project", return_value=Path("/fake")),
            patch("orchestrator.flow.load_flow", return_value=mock_flow),
            patch("orchestrator.scheduler._evaluate_project_script_condition", return_value=True),
            patch("orchestrator.steps.execute_steps"),
        ):
            from orchestrator.scheduler import check_project_completion
            check_project_completion()

        sdk.projects.update.assert_called_once_with(project_id, status="provisional")

    def test_multiple_projects_processed_independently(self):
        """Multiple active projects are each checked independently."""
        proj1_id = "PROJ-001"
        proj2_id = "PROJ-002"
        proj1 = {"id": proj1_id, "status": "active", "branch": "feature/001", "title": "Project 1"}
        proj2 = {"id": proj2_id, "status": "active", "branch": "feature/002", "title": "Project 2"}

        sdk = _make_sdk(
            projects=[proj1, proj2],
            tasks_by_project={
                proj1_id: [{"id": "TASK-1", "queue": "done"}],
                proj2_id: [{"id": "TASK-2", "queue": "incoming"}],  # not done
            },
            project_by_id={proj1_id: proj1},
        )

        mock_flow = _make_flow("children_complete", "provisional", runs=["create_project_pr"])

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.scheduler.find_parent_project", return_value=Path("/fake")),
            patch("orchestrator.flow.load_flow", return_value=mock_flow),
            patch("orchestrator.steps.execute_steps"),
        ):
            from orchestrator.scheduler import check_project_completion
            check_project_completion()

        # Only proj1 should be updated (proj2 has tasks not done)
        sdk.projects.update.assert_called_once_with(proj1_id, status="provisional")

    def test_manual_condition_blocks_automatic_transition(self):
        """Manual conditions require explicit human approval; they block automatic transition."""
        project_id = "PROJ-manual"
        project = {"id": project_id, "status": "active", "branch": "feature/manual", "title": "Manual"}

        sdk = _make_sdk(
            projects=[project],
            tasks_by_project={project_id: [{"id": "TASK-1", "queue": "done"}]},
        )

        mock_condition = MagicMock()
        mock_condition.name = "human_approval"
        mock_condition.type = "manual"

        mock_flow = _make_flow(
            "children_complete", "provisional",
            runs=["create_project_pr"],
            conditions=[mock_condition],
        )

        with (
            patch("orchestrator.scheduler.queue_utils.get_sdk", return_value=sdk),
            patch("orchestrator.scheduler.find_parent_project", return_value=Path("/fake")),
            patch("orchestrator.flow.load_flow", return_value=mock_flow),
            patch("orchestrator.steps.execute_steps") as mock_execute_steps,
        ):
            from orchestrator.scheduler import check_project_completion
            check_project_completion()

        mock_execute_steps.assert_not_called()
        sdk.projects.update.assert_not_called()


class TestEvaluateProjectScriptCondition:
    """Tests for _evaluate_project_script_condition()."""

    def test_condition_without_script_passes(self):
        """A condition with no script name passes by default."""
        from orchestrator.scheduler import _evaluate_project_script_condition

        condition = MagicMock()
        condition.name = "empty_condition"
        condition.script = None

        result = _evaluate_project_script_condition(condition, Path("/fake"), "PROJ-1")
        assert result is True

    def test_run_tests_with_no_test_runner_passes(self, tmp_path):
        """run-tests condition passes when no test runner is detected."""
        from orchestrator.scheduler import _evaluate_project_script_condition

        condition = MagicMock()
        condition.name = "all_tests_pass"
        condition.script = "run-tests"

        # tmp_path has no test runner files → passes by default
        result = _evaluate_project_script_condition(condition, tmp_path, "PROJ-1")
        assert result is True

    def test_run_tests_passes_on_success(self, tmp_path):
        """run-tests condition passes when the test command exits 0."""
        from orchestrator.scheduler import _evaluate_project_script_condition

        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

        condition = MagicMock()
        condition.name = "all_tests_pass"
        condition.script = "run-tests"

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("orchestrator.scheduler.subprocess.run", return_value=mock_result):
            result = _evaluate_project_script_condition(condition, tmp_path, "PROJ-1")

        assert result is True

    def test_run_tests_fails_on_nonzero_exit(self, tmp_path):
        """run-tests condition fails when the test command exits non-zero."""
        from orchestrator.scheduler import _evaluate_project_script_condition

        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

        condition = MagicMock()
        condition.name = "all_tests_pass"
        condition.script = "run-tests"

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "FAILED test_foo"
        mock_result.stderr = ""

        with patch("orchestrator.scheduler.subprocess.run", return_value=mock_result):
            result = _evaluate_project_script_condition(condition, tmp_path, "PROJ-1")

        assert result is False

    def test_timeout_returns_false(self, tmp_path):
        """run-tests condition fails gracefully on timeout."""
        import subprocess as subprocess_mod
        from orchestrator.scheduler import _evaluate_project_script_condition

        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

        condition = MagicMock()
        condition.name = "all_tests_pass"
        condition.script = "run-tests"

        with patch(
            "orchestrator.scheduler.subprocess.run",
            side_effect=subprocess_mod.TimeoutExpired("pytest", 300),
        ):
            result = _evaluate_project_script_condition(condition, tmp_path, "PROJ-1")

        assert result is False


class TestApproveProjectViaFlow:
    """Tests for approve_project_via_flow() in projects.py."""

    def test_project_not_found_returns_error(self):
        from orchestrator.projects import approve_project_via_flow

        with patch("orchestrator.projects.get_project", return_value=None):
            result = approve_project_via_flow("PROJ-missing")

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_flow_not_found_returns_error(self):
        from orchestrator.projects import approve_project_via_flow

        project = {"id": "PROJ-1", "flow": "project"}

        with (
            patch("orchestrator.projects.get_project", return_value=project),
            patch("orchestrator.flow.load_flow", side_effect=FileNotFoundError("not found")),
        ):
            result = approve_project_via_flow("PROJ-1")

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_no_transition_from_provisional_returns_error(self):
        from orchestrator.projects import approve_project_via_flow

        project = {"id": "PROJ-1", "flow": "project"}

        mock_flow = MagicMock()
        mock_flow.get_transitions_from.return_value = []  # No transition

        with (
            patch("orchestrator.projects.get_project", return_value=project),
            patch("orchestrator.flow.load_flow", return_value=mock_flow),
        ):
            result = approve_project_via_flow("PROJ-1")

        assert result["success"] is False
        assert "No transition" in result["error"]

    def test_successful_approval_runs_steps_and_updates_status(self):
        """Successful approval runs transition steps and updates project to done."""
        from orchestrator.projects import approve_project_via_flow

        project = {"id": "PROJ-1", "flow": "project", "pr_number": 42}
        sdk = MagicMock()
        sdk.projects.update.return_value = {}

        mock_transition = MagicMock()
        mock_transition.to_state = "done"
        mock_transition.runs = ["merge_project_pr"]

        mock_flow = MagicMock()
        mock_flow.get_transitions_from.side_effect = (
            lambda state: [mock_transition] if state == "provisional" else []
        )

        with (
            patch("orchestrator.projects.get_project", return_value=project),
            patch("orchestrator.flow.load_flow", return_value=mock_flow),
            patch("orchestrator.steps.execute_steps") as mock_execute_steps,
            patch("orchestrator.projects.get_sdk", return_value=sdk),
            patch("orchestrator.config.find_parent_project", return_value=Path("/fake")),
        ):
            result = approve_project_via_flow("PROJ-1")

        assert result["success"] is True
        assert result["new_status"] == "done"
        mock_execute_steps.assert_called_once_with(
            ["merge_project_pr"], project, {}, Path("/fake")
        )
        sdk.projects.update.assert_called_once_with("PROJ-1", status="done")

    def test_step_failure_returns_error(self):
        """If a step raises, approve_project_via_flow returns an error dict."""
        from orchestrator.projects import approve_project_via_flow

        project = {"id": "PROJ-1", "flow": "project", "pr_number": 42}

        mock_transition = MagicMock()
        mock_transition.to_state = "done"
        mock_transition.runs = ["merge_project_pr"]

        mock_flow = MagicMock()
        mock_flow.get_transitions_from.return_value = [mock_transition]

        sdk = MagicMock()

        with (
            patch("orchestrator.projects.get_project", return_value=project),
            patch("orchestrator.flow.load_flow", return_value=mock_flow),
            patch("orchestrator.steps.execute_steps", side_effect=RuntimeError("merge failed")),
            patch("orchestrator.projects.get_sdk", return_value=sdk),
            patch("orchestrator.config.find_parent_project", return_value=Path("/fake")),
        ):
            result = approve_project_via_flow("PROJ-1")

        assert result["success"] is False
        assert "merge failed" in result["error"]
        sdk.projects.update.assert_not_called()
