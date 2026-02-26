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
        from octopoid.steps import STEP_REGISTRY

        assert "post_review_comment" in STEP_REGISTRY
        assert "check_ci" in STEP_REGISTRY
        assert "merge_pr" in STEP_REGISTRY
        assert "reject_with_feedback" in STEP_REGISTRY

    def test_implementer_steps_registered(self):
        """Implementer steps are in the registry."""
        from octopoid.steps import STEP_REGISTRY

        assert "push_branch" in STEP_REGISTRY
        assert "run_tests" in STEP_REGISTRY
        assert "create_pr" in STEP_REGISTRY

    def test_execute_steps_unknown_step_raises(self):
        """execute_steps raises ValueError for unknown step names."""
        from octopoid.steps import execute_steps

        with pytest.raises(ValueError, match="Unknown step: nonexistent"):
            execute_steps(["nonexistent"], {}, {}, Path("/tmp"))

    def test_execute_steps_calls_in_order(self):
        """execute_steps calls steps in listed order."""
        from octopoid.steps import STEP_REGISTRY, execute_steps

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
        from octopoid.steps import _build_node_path

        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False):
            result = _build_node_path()
        assert "/usr/bin:/bin" in result

    def test_includes_nvm_bin_when_present(self, tmp_path):
        """_build_node_path adds nvm node bin directory when nvm is installed."""
        from octopoid.steps import _build_node_path

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
        from octopoid.steps import _build_node_path

        shims_path = Path("/usr/local/lib/node_modules/corepack/shims")
        result = _build_node_path()

        if shims_path.is_dir():
            assert str(shims_path) in result
        else:
            # shims not installed — just verify the function still returns a string
            assert isinstance(result, str)

    def test_returns_string(self):
        """_build_node_path always returns a string."""
        from octopoid.steps import _build_node_path

        result = _build_node_path()
        assert isinstance(result, str)


class TestRunTestsStep:
    """Tests for the run_tests step."""

    def test_run_tests_skips_when_no_runner(self, tmp_path):
        """run_tests skips gracefully when no test runner is detected."""
        from octopoid.steps import run_tests

        task_dir = tmp_path
        worktree = task_dir / "worktree"
        worktree.mkdir()
        # No pytest.ini, pyproject.toml, package.json, or Makefile

        # Should not raise
        run_tests({}, {}, task_dir)

    def test_run_tests_raises_on_failure(self, tmp_path):
        """run_tests raises RuntimeError when tests fail."""
        from octopoid.steps import run_tests

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

        with patch("octopoid.steps.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="Tests failed"):
                run_tests({}, {}, task_dir)

    def test_run_tests_timeout_raises_runtime_error(self, tmp_path):
        """run_tests raises RuntimeError when subprocess times out."""
        from octopoid.steps import run_tests

        task_dir = tmp_path
        worktree = task_dir / "worktree"
        worktree.mkdir()
        (worktree / "pytest.ini").write_text("[pytest]\n")

        with patch(
            "octopoid.steps.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd=["python", "-m", "pytest"], timeout=300
            ),
        ):
            with pytest.raises(RuntimeError, match="timed out"):
                run_tests({}, {}, task_dir)

    def test_run_tests_success_path(self, tmp_path):
        """run_tests completes without error when tests pass (exit code 0)."""
        from octopoid.steps import run_tests

        task_dir = tmp_path
        worktree = task_dir / "worktree"
        worktree.mkdir()
        (worktree / "pytest.ini").write_text("[pytest]\n")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1 passed"
        mock_result.stderr = ""

        with patch("octopoid.steps.subprocess.run", return_value=mock_result):
            # Should not raise
            run_tests({}, {}, task_dir)

    def test_run_tests_passes_augmented_path_env(self, tmp_path):
        """run_tests passes an env with augmented PATH to subprocess."""
        from octopoid.steps import run_tests

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

        with patch("octopoid.steps.subprocess.run", side_effect=capture_env):
            run_tests({}, {}, task_dir)

        assert "PATH" in captured_env
        assert captured_env["PATH"]  # not empty


class TestMergePrStep:
    """Tests for the merge_pr step."""

    def test_merge_pr_raises_on_error_dict(self, mock_sdk_for_unit_tests):
        """merge_pr raises RuntimeError when approve_and_merge returns an error dict."""
        from octopoid.steps import merge_pr

        error_result = {"error": "BEFORE_MERGE hooks failed", "merged": False}

        with patch("octopoid.queue_utils.approve_and_merge", return_value=error_result):
            with pytest.raises(RuntimeError, match="BEFORE_MERGE hooks failed"):
                merge_pr({"id": "TASK-test"}, {}, Path("/tmp"))

    def test_merge_pr_succeeds_silently_when_merged(self, mock_sdk_for_unit_tests):
        """merge_pr returns None (silently) when approve_and_merge returns merged=True."""
        from octopoid.steps import merge_pr

        success_result = {"merged": True, "task_id": "TASK-test"}

        with patch("octopoid.queue_utils.approve_and_merge", return_value=success_result):
            result = merge_pr({"id": "TASK-test"}, {}, Path("/tmp"))

        assert result is None


class TestCreatePrStep:
    """Tests for the create_pr step."""

    def test_create_pr_updates_task_metadata(self, tmp_path, mock_sdk_for_unit_tests):
        """create_pr stores pr_url and pr_number on the task via sdk.tasks.update."""
        from octopoid.repo_manager import PrInfo
        from octopoid.steps import create_pr

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
        with patch("octopoid.repo_manager.RepoManager", mock_repo_cls):
            create_pr(task, {}, task_dir)

        mock_sdk_for_unit_tests.tasks.update.assert_called_once_with(
            "TASK-test456",
            pr_url="https://github.com/test/repo/pull/42",
            pr_number=42,
        )

    def test_create_pr_passes_task_branch_to_repo_manager(self, tmp_path, mock_sdk_for_unit_tests):
        """create_pr passes task branch to RepoManager as base_branch."""
        from octopoid.repo_manager import PrInfo
        from octopoid.steps import create_pr

        task_dir = tmp_path
        worktree = task_dir / "worktree"
        worktree.mkdir()

        mock_repo = MagicMock()
        mock_repo.create_pr.return_value = PrInfo(
            url="https://github.com/test/repo/pull/99",
            number=99,
            created=True,
        )
        mock_repo_cls = MagicMock(return_value=mock_repo)

        task = {"id": "TASK-branch-test", "title": "Branch test", "branch": "feature/foo"}
        with patch("octopoid.repo_manager.RepoManager", mock_repo_cls):
            create_pr(task, {}, task_dir)

        mock_repo_cls.assert_called_once_with(worktree, base_branch="feature/foo")

    def test_create_pr_defaults_to_main_when_no_branch(self, tmp_path, mock_sdk_for_unit_tests):
        """create_pr defaults to main when task has no branch field."""
        from octopoid.repo_manager import PrInfo
        from octopoid.steps import create_pr

        task_dir = tmp_path
        worktree = task_dir / "worktree"
        worktree.mkdir()

        mock_repo = MagicMock()
        mock_repo.create_pr.return_value = PrInfo(
            url="https://github.com/test/repo/pull/100",
            number=100,
            created=True,
        )
        mock_repo_cls = MagicMock(return_value=mock_repo)

        task = {"id": "TASK-no-branch", "title": "No branch field"}
        with patch("octopoid.repo_manager.RepoManager", mock_repo_cls):
            create_pr(task, {}, task_dir)

        mock_repo_cls.assert_called_once_with(worktree, base_branch="main")


class TestCheckCiStep:
    """Tests for the check_ci step."""

    def test_check_ci_no_pr_is_noop(self, tmp_path):
        """check_ci skips gracefully when no pr_number on task."""
        from octopoid.steps import check_ci

        with patch("octopoid.steps.subprocess.run") as mock_run:
            check_ci({}, {}, tmp_path)
            mock_run.assert_not_called()

    def test_check_ci_no_pr_number_key_is_noop(self, tmp_path):
        """check_ci skips gracefully when pr_number is None."""
        from octopoid.steps import check_ci

        with patch("octopoid.steps.subprocess.run") as mock_run:
            check_ci({"pr_number": None}, {}, tmp_path)
            mock_run.assert_not_called()

    def test_check_ci_raises_retryable_on_pending(self, tmp_path):
        """check_ci raises RetryableStepError when a CI check is in progress."""
        import json as _json
        from octopoid.steps import RetryableStepError, check_ci

        checks = [{"name": "test-suite", "state": "IN_PROGRESS", "conclusion": None}]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _json.dumps(checks)
        mock_result.stderr = ""

        with patch("octopoid.steps.subprocess.run", return_value=mock_result):
            with pytest.raises(RetryableStepError, match="pending"):
                check_ci({"pr_number": 42}, {}, tmp_path)

    def test_check_ci_raises_retryable_on_queued(self, tmp_path):
        """check_ci raises RetryableStepError when a CI check is queued."""
        import json as _json
        from octopoid.steps import RetryableStepError, check_ci

        checks = [{"name": "build", "state": "QUEUED", "conclusion": None}]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _json.dumps(checks)
        mock_result.stderr = ""

        with patch("octopoid.steps.subprocess.run", return_value=mock_result):
            with pytest.raises(RetryableStepError, match="pending"):
                check_ci({"pr_number": 99}, {}, tmp_path)

    def test_check_ci_raises_runtime_error_on_failure(self, tmp_path):
        """check_ci raises RuntimeError with the failed check name when CI failed."""
        import json as _json
        from octopoid.steps import check_ci

        checks = [
            {"name": "lint", "state": "COMPLETED", "conclusion": "FAILURE"},
        ]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _json.dumps(checks)
        mock_result.stderr = ""

        with patch("octopoid.steps.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="lint"):
                check_ci({"pr_number": 42}, {}, tmp_path)

    def test_check_ci_raises_runtime_error_names_all_failed_checks(self, tmp_path):
        """check_ci includes all failed check names in the error message."""
        import json as _json
        from octopoid.steps import check_ci

        checks = [
            {"name": "unit-tests", "state": "COMPLETED", "conclusion": "FAILURE"},
            {"name": "integration-tests", "state": "COMPLETED", "conclusion": "TIMED_OUT"},
        ]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _json.dumps(checks)
        mock_result.stderr = ""

        with patch("octopoid.steps.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError) as exc_info:
                check_ci({"pr_number": 42}, {}, tmp_path)

        msg = str(exc_info.value)
        assert "unit-tests" in msg
        assert "integration-tests" in msg

    def test_check_ci_succeeds_when_all_pass(self, tmp_path):
        """check_ci completes without error when all CI checks passed."""
        import json as _json
        from octopoid.steps import check_ci

        checks = [
            {"name": "unit-tests", "state": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "lint", "state": "COMPLETED", "conclusion": "SUCCESS"},
        ]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _json.dumps(checks)
        mock_result.stderr = ""

        with patch("octopoid.steps.subprocess.run", return_value=mock_result):
            # Should not raise
            check_ci({"pr_number": 42}, {}, tmp_path)

    def test_check_ci_succeeds_when_checks_skipped_or_neutral(self, tmp_path):
        """check_ci treats SKIPPED and NEUTRAL conclusions as passing."""
        import json as _json
        from octopoid.steps import check_ci

        checks = [
            {"name": "optional-check", "state": "COMPLETED", "conclusion": "SKIPPED"},
            {"name": "docs-check", "state": "COMPLETED", "conclusion": "NEUTRAL"},
        ]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _json.dumps(checks)
        mock_result.stderr = ""

        with patch("octopoid.steps.subprocess.run", return_value=mock_result):
            # Should not raise
            check_ci({"pr_number": 42}, {}, tmp_path)

    def test_check_ci_noop_when_no_checks_configured(self, tmp_path):
        """check_ci proceeds without error when the PR has no CI checks."""
        import json as _json
        from octopoid.steps import check_ci

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _json.dumps([])
        mock_result.stderr = ""

        with patch("octopoid.steps.subprocess.run", return_value=mock_result):
            # Should not raise
            check_ci({"pr_number": 42}, {}, tmp_path)

    def test_check_ci_failed_and_pending_reports_failure_first(self, tmp_path):
        """check_ci raises RuntimeError (not RetryableStepError) when some checks failed
        even if other checks are still pending."""
        import json as _json
        from octopoid.steps import RetryableStepError, check_ci

        checks = [
            {"name": "lint", "state": "COMPLETED", "conclusion": "FAILURE"},
            {"name": "tests", "state": "IN_PROGRESS", "conclusion": None},
        ]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _json.dumps(checks)
        mock_result.stderr = ""

        with patch("octopoid.steps.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError) as exc_info:
                check_ci({"pr_number": 42}, {}, tmp_path)
            # Must be RuntimeError, not RetryableStepError
            assert not isinstance(exc_info.value, RetryableStepError)
            assert "lint" in str(exc_info.value)


class TestUpdateChangelogStep:
    """Tests for the update_changelog step."""

    def test_skips_when_no_changes_file(self, tmp_path):
        """update_changelog skips silently when changes.md does not exist."""
        from octopoid.steps import update_changelog

        task_dir = tmp_path
        # No changes.md created

        with patch("octopoid.steps.subprocess.run") as mock_run:
            update_changelog({"id": "TASK-abc"}, {}, task_dir)
            mock_run.assert_not_called()

    def test_skips_when_changes_file_is_empty(self, tmp_path):
        """update_changelog skips silently when changes.md is empty."""
        from octopoid.steps import update_changelog

        task_dir = tmp_path
        (task_dir / "changes.md").write_text("   \n  ")

        with patch("octopoid.steps.subprocess.run") as mock_run:
            update_changelog({"id": "TASK-abc"}, {}, task_dir)
            mock_run.assert_not_called()

    def test_skips_when_changelog_not_found(self, tmp_path):
        """update_changelog skips when CHANGELOG.md does not exist in project root."""
        from octopoid.steps import update_changelog

        task_dir = tmp_path
        project_root = tmp_path / "project"
        project_root.mkdir()
        (task_dir / "changes.md").write_text("### Added\n- New feature\n")
        # No CHANGELOG.md in project_root

        with patch("octopoid.config.find_parent_project", return_value=project_root), \
             patch("octopoid.config.get_base_branch", return_value="main"), \
             patch("octopoid.steps.subprocess.run") as mock_run:
            update_changelog({"id": "TASK-abc"}, {}, task_dir)
            mock_run.assert_not_called()

    def test_skips_when_no_unreleased_section(self, tmp_path):
        """update_changelog skips when CHANGELOG.md has no ## [Unreleased] section."""
        from octopoid.steps import update_changelog

        task_dir = tmp_path
        project_root = tmp_path / "project"
        project_root.mkdir()
        (task_dir / "changes.md").write_text("### Added\n- New feature\n")
        (project_root / "CHANGELOG.md").write_text("# Changelog\n\n## [1.0.0]\n- Initial release\n")

        ok = MagicMock()
        ok.returncode = 0
        ok.stdout = ""
        ok.stderr = ""

        with patch("octopoid.config.find_parent_project", return_value=project_root), \
             patch("octopoid.config.get_base_branch", return_value="main"), \
             patch("octopoid.steps.subprocess.run", return_value=ok) as mock_run:
            update_changelog({"id": "TASK-abc"}, {}, task_dir)

        # git fetch and pull run, but no commit or push
        called_cmds = [tuple(c.args[0]) for c in mock_run.call_args_list]
        assert not any("commit" in cmd for cmd in called_cmds)

    def test_inserts_changes_after_unreleased_header(self, tmp_path):
        """update_changelog prepends changes.md content under ## [Unreleased]."""
        from octopoid.steps import update_changelog

        task_dir = tmp_path
        project_root = tmp_path / "project"
        project_root.mkdir()

        changelog_initial = (
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            "### Changed\n\n- Existing change\n\n"
            "## [1.0.0]\n- Initial release\n"
        )
        (project_root / "CHANGELOG.md").write_text(changelog_initial)
        (task_dir / "changes.md").write_text("### Added\n\n- New feature\n")

        ok = MagicMock()
        ok.returncode = 0
        ok.stdout = ""
        ok.stderr = ""

        with patch("octopoid.config.find_parent_project", return_value=project_root), \
             patch("octopoid.config.get_base_branch", return_value="main"), \
             patch("octopoid.steps.subprocess.run", return_value=ok):
            update_changelog({"id": "TASK-abc", "title": "My feature"}, {}, task_dir)

        result = (project_root / "CHANGELOG.md").read_text()
        unreleased_idx = result.index("## [Unreleased]")
        added_idx = result.index("### Added")
        existing_idx = result.index("### Changed")
        # New content should appear between ## [Unreleased] and the existing content
        assert unreleased_idx < added_idx < existing_idx

    def test_commits_and_pushes_on_success(self, tmp_path):
        """update_changelog commits and pushes when changes are applied."""
        from octopoid.steps import update_changelog

        task_dir = tmp_path
        project_root = tmp_path / "project"
        project_root.mkdir()

        (project_root / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [Unreleased]\n\n## [1.0.0]\n- Initial\n"
        )
        (task_dir / "changes.md").write_text("### Fixed\n\n- Bug fix\n")

        ok = MagicMock()
        ok.returncode = 0
        ok.stdout = ""
        ok.stderr = ""

        with patch("octopoid.config.find_parent_project", return_value=project_root), \
             patch("octopoid.config.get_base_branch", return_value="main"), \
             patch("octopoid.steps.subprocess.run", return_value=ok) as mock_run:
            update_changelog({"id": "TASK-xyz", "title": "Bug fix"}, {}, task_dir)

        called_cmds = [c.args[0] for c in mock_run.call_args_list]
        # Should have called: fetch, pull, add, commit, push
        cmd_strings = [" ".join(c) for c in called_cmds]
        assert any("fetch" in s for s in cmd_strings)
        assert any("pull" in s for s in cmd_strings)
        assert any("add" in s for s in cmd_strings)
        assert any("commit" in s for s in cmd_strings)
        assert any("push" in s for s in cmd_strings)

    def test_raises_on_fetch_failure(self, tmp_path):
        """update_changelog raises RuntimeError when git fetch fails."""
        from octopoid.steps import update_changelog

        task_dir = tmp_path
        project_root = tmp_path / "project"
        project_root.mkdir()

        (project_root / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n")
        (task_dir / "changes.md").write_text("### Added\n\n- Feature\n")

        fail = MagicMock()
        fail.returncode = 1
        fail.stdout = ""
        fail.stderr = "fatal: could not read"

        with patch("octopoid.config.find_parent_project", return_value=project_root), \
             patch("octopoid.config.get_base_branch", return_value="main"), \
             patch("octopoid.steps.subprocess.run", return_value=fail):
            with pytest.raises(RuntimeError, match="git fetch failed"):
                update_changelog({"id": "TASK-abc"}, {}, task_dir)

    def test_update_changelog_step_is_registered(self):
        """update_changelog is registered in STEP_REGISTRY."""
        from octopoid.steps import STEP_REGISTRY

        assert "update_changelog" in STEP_REGISTRY


class TestAggregateChildChangesStep:
    """Tests for the aggregate_child_changes step."""

    def test_step_is_registered(self):
        """aggregate_child_changes is registered in STEP_REGISTRY."""
        from octopoid.steps import STEP_REGISTRY

        assert "aggregate_child_changes" in STEP_REGISTRY

    def test_skips_when_no_project_id(self, tmp_path, mock_sdk_for_unit_tests):
        """aggregate_child_changes skips silently when task has no id."""
        from octopoid.steps import aggregate_child_changes

        aggregate_child_changes({}, {}, tmp_path)
        mock_sdk_for_unit_tests.projects.get_tasks.assert_not_called()

    def test_skips_when_no_child_tasks(self, tmp_path, mock_sdk_for_unit_tests):
        """aggregate_child_changes skips when project has no child tasks."""
        from octopoid.steps import aggregate_child_changes

        mock_sdk_for_unit_tests.projects.get_tasks.return_value = []

        aggregate_child_changes({"id": "PROJ-abc"}, {}, tmp_path)

        assert not (tmp_path / "changes.md").exists()

    def test_skips_when_no_child_changes_files(self, tmp_path, mock_sdk_for_unit_tests):
        """aggregate_child_changes skips when no child has a changes.md."""
        from octopoid.steps import aggregate_child_changes

        mock_sdk_for_unit_tests.projects.get_tasks.return_value = [
            {"id": "TASK-child1"},
            {"id": "TASK-child2"},
        ]

        with patch("octopoid.config.get_tasks_dir", return_value=tmp_path / "tasks"):
            aggregate_child_changes({"id": "PROJ-abc"}, {}, tmp_path)

        assert not (tmp_path / "changes.md").exists()

    def test_aggregates_child_changes_into_task_dir(self, tmp_path, mock_sdk_for_unit_tests):
        """aggregate_child_changes writes concatenated child changes to task_dir/changes.md."""
        from octopoid.steps import aggregate_child_changes

        tasks_dir = tmp_path / "tasks"
        child1_dir = tasks_dir / "TASK-child1"
        child2_dir = tasks_dir / "TASK-child2"
        child1_dir.mkdir(parents=True)
        child2_dir.mkdir(parents=True)
        (child1_dir / "changes.md").write_text("### Added\n\n- Feature A\n")
        (child2_dir / "changes.md").write_text("### Fixed\n\n- Bug fix B\n")

        mock_sdk_for_unit_tests.projects.get_tasks.return_value = [
            {"id": "TASK-child1"},
            {"id": "TASK-child2"},
        ]

        project_dir = tmp_path / "project_root"
        project_dir.mkdir()

        with patch("octopoid.config.get_tasks_dir", return_value=tasks_dir):
            aggregate_child_changes({"id": "PROJ-abc"}, {}, project_dir)

        result = (project_dir / "changes.md").read_text()
        assert "Feature A" in result
        assert "Bug fix B" in result

    def test_skips_children_without_id(self, tmp_path, mock_sdk_for_unit_tests):
        """aggregate_child_changes skips child task entries with no id field."""
        from octopoid.steps import aggregate_child_changes

        tasks_dir = tmp_path / "tasks"
        child_dir = tasks_dir / "TASK-real"
        child_dir.mkdir(parents=True)
        (child_dir / "changes.md").write_text("### Added\n\n- Real feature\n")

        mock_sdk_for_unit_tests.projects.get_tasks.return_value = [
            {},  # no id
            {"id": "TASK-real"},
        ]

        project_dir = tmp_path / "project_root"
        project_dir.mkdir()

        with patch("octopoid.config.get_tasks_dir", return_value=tasks_dir):
            aggregate_child_changes({"id": "PROJ-abc"}, {}, project_dir)

        result = (project_dir / "changes.md").read_text()
        assert "Real feature" in result

    def test_skips_empty_child_changes_files(self, tmp_path, mock_sdk_for_unit_tests):
        """aggregate_child_changes ignores child changes.md files that are empty."""
        from octopoid.steps import aggregate_child_changes

        tasks_dir = tmp_path / "tasks"
        child1_dir = tasks_dir / "TASK-empty"
        child2_dir = tasks_dir / "TASK-content"
        child1_dir.mkdir(parents=True)
        child2_dir.mkdir(parents=True)
        (child1_dir / "changes.md").write_text("   \n\n  ")
        (child2_dir / "changes.md").write_text("### Added\n\n- Something\n")

        mock_sdk_for_unit_tests.projects.get_tasks.return_value = [
            {"id": "TASK-empty"},
            {"id": "TASK-content"},
        ]

        project_dir = tmp_path / "project_root"
        project_dir.mkdir()

        with patch("octopoid.config.get_tasks_dir", return_value=tasks_dir):
            aggregate_child_changes({"id": "PROJ-abc"}, {}, project_dir)

        result = (project_dir / "changes.md").read_text()
        assert "Something" in result
        # Only one non-empty part — no double newline separator
        assert result.strip() == "### Added\n\n- Something"

    def test_handles_sdk_error_gracefully(self, tmp_path, mock_sdk_for_unit_tests):
        """aggregate_child_changes skips gracefully when SDK call fails."""
        from octopoid.steps import aggregate_child_changes

        mock_sdk_for_unit_tests.projects.get_tasks.side_effect = RuntimeError("network error")

        # Should not raise
        aggregate_child_changes({"id": "PROJ-abc"}, {}, tmp_path)
        assert not (tmp_path / "changes.md").exists()
