"""Tests for orchestrator.steps — step registry and implementer steps."""

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestStepRegistry:
    """Tests for the step registry itself."""

    def test_gatekeeper_steps_registered(self):
        """Gatekeeper steps are in the registry."""
        from orchestrator.steps import STEP_REGISTRY

        assert "post_review_comment" in STEP_REGISTRY
        assert "merge_pr" in STEP_REGISTRY
        assert "reject_with_feedback" in STEP_REGISTRY

    def test_implementer_steps_registered(self):
        """Implementer steps are in the registry."""
        from orchestrator.steps import STEP_REGISTRY

        assert "push_branch" in STEP_REGISTRY
        assert "run_tests" in STEP_REGISTRY
        assert "create_pr" in STEP_REGISTRY
        assert "submit_to_server" in STEP_REGISTRY

    def test_execute_steps_unknown_step_raises(self):
        """execute_steps raises ValueError for unknown step names."""
        from orchestrator.steps import execute_steps

        with pytest.raises(ValueError, match="Unknown step: nonexistent"):
            execute_steps(["nonexistent"], {}, {}, Path("/tmp"))

    def test_execute_steps_calls_in_order(self):
        """execute_steps calls steps in listed order."""
        from orchestrator.steps import STEP_REGISTRY, execute_steps

        call_order = []

        original_push = STEP_REGISTRY.get("push_branch")
        original_run = STEP_REGISTRY.get("run_tests")

        try:
            STEP_REGISTRY["push_branch"] = lambda t, r, d: call_order.append("push_branch")
            STEP_REGISTRY["run_tests"] = lambda t, r, d: call_order.append("run_tests")

            execute_steps(["push_branch", "run_tests"], {}, {}, Path("/tmp"))
            assert call_order == ["push_branch", "run_tests"]
        finally:
            if original_push is not None:
                STEP_REGISTRY["push_branch"] = original_push
            if original_run is not None:
                STEP_REGISTRY["run_tests"] = original_run


class TestBuildNodePath:
    """Tests for _build_node_path."""

    def test_includes_existing_path(self):
        """_build_node_path includes the existing PATH."""
        from orchestrator.steps import _build_node_path

        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False):
            result = _build_node_path()
        assert "/usr/bin:/bin" in result

    def test_includes_nvm_bin_when_present(self, tmp_path):
        """_build_node_path adds nvm node bin directory when nvm is installed."""
        from orchestrator.steps import _build_node_path

        # Create a fake nvm directory structure
        nvm_bin = tmp_path / "versions" / "node" / "v20.0.0" / "bin"
        nvm_bin.mkdir(parents=True)

        with patch.dict(os.environ, {"NVM_DIR": str(tmp_path), "PATH": "/usr/bin"}, clear=False):
            result = _build_node_path()

        assert str(nvm_bin) in result
        # nvm bin should come before the existing PATH
        assert result.index(str(nvm_bin)) < result.index("/usr/bin")

    def test_corepack_shims_included_when_on_disk(self):
        """_build_node_path includes /usr/local corepack shims when they exist on disk."""
        from orchestrator.steps import _build_node_path

        shims_path = Path("/usr/local/lib/node_modules/corepack/shims")
        result = _build_node_path()

        if shims_path.is_dir():
            assert str(shims_path) in result
        else:
            # shims not installed — just verify the function still returns a string
            assert isinstance(result, str)

    def test_returns_string(self):
        """_build_node_path always returns a string."""
        from orchestrator.steps import _build_node_path

        result = _build_node_path()
        assert isinstance(result, str)


class TestRunTestsStep:
    """Tests for the run_tests step."""

    def test_run_tests_skips_when_no_runner(self, tmp_path):
        """run_tests skips gracefully when no test runner is detected."""
        from orchestrator.steps import run_tests

        task_dir = tmp_path
        worktree = task_dir / "worktree"
        worktree.mkdir()
        # No pytest.ini, pyproject.toml, package.json, or Makefile

        # Should not raise
        run_tests({}, {}, task_dir)

    def test_run_tests_raises_on_failure(self, tmp_path):
        """run_tests raises RuntimeError when tests fail."""
        from orchestrator.steps import run_tests

        task_dir = tmp_path
        worktree = task_dir / "worktree"
        worktree.mkdir()
        # Create a pytest.ini to trigger pytest detection
        (worktree / "pytest.ini").write_text("[pytest]\n")

        # Simulate a failing subprocess (exit code 1)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "FAILED test_fail.py::test_fail"
        mock_result.stderr = ""

        with patch("orchestrator.steps.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="Tests failed"):
                run_tests({}, {}, task_dir)

    def test_run_tests_passes_augmented_path_env(self, tmp_path):
        """run_tests passes an env with augmented PATH to subprocess."""
        from orchestrator.steps import run_tests

        task_dir = tmp_path
        worktree = task_dir / "worktree"
        worktree.mkdir()
        (worktree / "pytest.ini").write_text("[pytest]\n")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        captured_env = {}

        def capture_env(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return mock_result

        with patch("orchestrator.steps.subprocess.run", side_effect=capture_env):
            run_tests({}, {}, task_dir)

        assert "PATH" in captured_env
        assert captured_env["PATH"]  # not empty


class TestSubmitToServerStep:
    """Tests for the submit_to_server step."""

    def test_submit_to_server_calls_sdk_submit(self, tmp_path, mock_sdk_for_unit_tests):
        """submit_to_server calls sdk.tasks.submit with task_id."""
        from orchestrator.steps import submit_to_server

        task_dir = tmp_path
        worktree = task_dir / "worktree"
        worktree.mkdir()
        # Create a minimal git repo structure so the git command doesn't crash
        subprocess.run(["git", "init"], cwd=worktree, capture_output=True)

        task = {"id": "TASK-test123"}
        submit_to_server(task, {}, task_dir)

        mock_sdk_for_unit_tests.tasks.submit.assert_called_once()
        call_args = mock_sdk_for_unit_tests.tasks.submit.call_args
        assert call_args[0][0] == "TASK-test123" or call_args[1].get("task_id") == "TASK-test123"


class TestCreatePrStep:
    """Tests for the create_pr step."""

    def test_create_pr_updates_task_metadata(self, tmp_path, mock_sdk_for_unit_tests):
        """create_pr stores pr_url and pr_number on the task via sdk.tasks.update."""
        from orchestrator.repo_manager import PrInfo
        from orchestrator.steps import create_pr

        task_dir = tmp_path
        worktree = task_dir / "worktree"
        worktree.mkdir()

        mock_repo = MagicMock()
        mock_repo.create_pr.return_value = PrInfo(
            url="https://github.com/test/repo/pull/42",
            number=42,
            created=True,
        )
        mock_repo_cls = MagicMock(return_value=mock_repo)

        task = {"id": "TASK-test456", "title": "Test PR creation"}
        with patch("orchestrator.repo_manager.RepoManager", mock_repo_cls):
            create_pr(task, {}, task_dir)

        mock_sdk_for_unit_tests.tasks.update.assert_called_once_with(
            "TASK-test456",
            pr_url="https://github.com/test/repo/pull/42",
            pr_number=42,
        )
