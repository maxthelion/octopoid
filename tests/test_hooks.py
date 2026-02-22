"""Tests for orchestrator.hooks — lifecycle hooks system."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from orchestrator.hook_manager import DEFAULT_HOOKS
from orchestrator.hooks import (
    HookContext,
    HookPoint,
    HookResult,
    HookStatus,
    BUILTIN_HOOKS,
    hook_create_pr,
    hook_merge_pr,
    hook_rebase_on_base,
    hook_run_tests,
    resolve_hooks,
    run_hooks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(worktree: Path | None = None, **overrides) -> HookContext:
    """Build a HookContext with sensible test defaults."""
    defaults = dict(
        task_id="TSK-001",
        task_title="Fix the widget",
        task_path="/tasks/TSK-001.md",
        task_type=None,
        branch_name="task/TSK-001",
        base_branch="main",
        worktree=worktree or Path("/tmp/fake-worktree"),
        agent_name="implementer-1",
        commits_count=3,
    )
    defaults.update(overrides)
    return HookContext(**defaults)


# ---------------------------------------------------------------------------
# hook_rebase_on_base
# ---------------------------------------------------------------------------


class TestHookRebaseOnBase:
    """Tests for the rebase_on_base built-in hook."""

    def test_rebase_skipped_when_up_to_date(self):
        """If behind count is 0, rebase is skipped."""
        ctx = _make_ctx()

        def mock_run_git(args, **kwargs):
            m = MagicMock()
            m.returncode = 0
            if "rev-list" in args:
                m.stdout = "0\n"
            else:
                m.stdout = ""
            m.stderr = ""
            return m

        with patch("orchestrator.git_utils.run_git", side_effect=mock_run_git):
            result = hook_rebase_on_base(ctx)

        assert result.status == HookStatus.SKIP
        assert "up to date" in result.message

    def test_rebase_success_and_force_pushes(self):
        """Successful rebase returns SUCCESS and force-pushes."""
        ctx = _make_ctx()
        calls = []

        def mock_run_git(args, **kwargs):
            calls.append(args)
            m = MagicMock()
            m.returncode = 0
            if "rev-list" in args:
                m.stdout = "5\n"
            else:
                m.stdout = ""
            m.stderr = ""
            return m

        with patch("orchestrator.git_utils.run_git", side_effect=mock_run_git):
            result = hook_rebase_on_base(ctx)

        assert result.status == HookStatus.SUCCESS
        assert "Rebased" in result.message
        # Verify force-push was called
        push_calls = [c for c in calls if "push" in c]
        assert len(push_calls) == 1
        assert "--force-with-lease" in push_calls[0]

    def test_rebase_conflict_returns_failure_no_remediation(self):
        """Rebase conflict returns FAILURE without remediation_prompt."""
        ctx = _make_ctx()

        def mock_run_git(args, **kwargs):
            if "fetch" in args:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "rev-list" in args:
                return MagicMock(returncode=0, stdout="3\n", stderr="")
            if "rebase" in args and "--abort" not in args:
                return MagicMock(returncode=1, stdout="", stderr="CONFLICT in widget.py")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("orchestrator.git_utils.run_git", side_effect=mock_run_git):
            result = hook_rebase_on_base(ctx)

        assert result.status == HookStatus.FAILURE
        assert "conflict" in result.message.lower() or "Rebase" in result.message
        assert result.remediation_prompt is None

    def test_fetch_failure(self):
        """If fetch fails, return FAILURE."""
        ctx = _make_ctx()

        def mock_run_git(args, **kwargs):
            if "fetch" in args:
                raise subprocess.CalledProcessError(1, args, output="", stderr="network error")
            return MagicMock(returncode=0, stdout="0\n", stderr="")

        with patch("orchestrator.git_utils.run_git", side_effect=mock_run_git):
            result = hook_rebase_on_base(ctx)

        assert result.status == HookStatus.FAILURE
        assert result.remediation_prompt is None

    def test_fetch_timeout(self):
        """Timeout during fetch returns FAILURE."""
        ctx = _make_ctx()

        def mock_run_git(args, **kwargs):
            if "fetch" in args:
                raise subprocess.TimeoutExpired(args, 60)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("orchestrator.git_utils.run_git", side_effect=mock_run_git):
            result = hook_rebase_on_base(ctx)

        assert result.status == HookStatus.FAILURE
        assert "Timeout" in result.message


# ---------------------------------------------------------------------------
# hook_create_pr
# ---------------------------------------------------------------------------


class TestHookCreatePr:
    """Tests for the create_pr built-in hook."""

    def test_success(self):
        """Successful PR creation returns SUCCESS with pr_url in context."""
        ctx = _make_ctx(extra={"stdout": "some output"})

        with patch("orchestrator.git_utils.create_pull_request", return_value="https://github.com/test/pr/1"):
            result = hook_create_pr(ctx)

        assert result.status == HookStatus.SUCCESS
        assert result.context["pr_url"] == "https://github.com/test/pr/1"

    def test_failure(self):
        """Failed PR creation returns FAILURE."""
        ctx = _make_ctx()

        with patch("orchestrator.git_utils.create_pull_request", side_effect=RuntimeError("push failed")):
            result = hook_create_pr(ctx)

        assert result.status == HookStatus.FAILURE
        assert "push failed" in result.message

    def test_long_stdout_truncated(self):
        """PR body truncates stdout to last 2000 chars."""
        long_stdout = "x" * 5000
        ctx = _make_ctx(extra={"stdout": long_stdout})

        with patch("orchestrator.git_utils.create_pull_request", return_value="https://example.com/pr/2") as mock_pr:
            hook_create_pr(ctx)

        # Check that the body passed to create_pull_request contains truncated output
        call_args = mock_pr.call_args
        body = call_args[0][4]  # 5th positional arg is body
        # The body should contain only the last 2000 chars of stdout
        assert len(body) < 5000


# ---------------------------------------------------------------------------
# hook_run_tests
# ---------------------------------------------------------------------------


class TestHookRunTests:
    """Tests for the run_tests built-in hook."""

    def test_no_test_runner_detected(self, tmp_path):
        """When no test config files exist, skip."""
        ctx = _make_ctx(worktree=tmp_path)
        result = hook_run_tests(ctx)
        assert result.status == HookStatus.SKIP

    def test_pytest_detected_and_passes(self, tmp_path):
        """When pyproject.toml exists, runs pytest and succeeds."""
        (tmp_path / "pyproject.toml").write_text("[tool.pytest]")
        ctx = _make_ctx(worktree=tmp_path)

        with patch("orchestrator.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="passed", stderr="")
            result = hook_run_tests(ctx)

        assert result.status == HookStatus.SUCCESS
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == ["python", "-m", "pytest", "--tb=short", "-q"]

    def test_pytest_detected_and_fails(self, tmp_path):
        """When tests fail, return FAILURE with remediation_prompt."""
        (tmp_path / "pytest.ini").write_text("")
        ctx = _make_ctx(worktree=tmp_path)

        with patch("orchestrator.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="FAILED test_foo.py", stderr=""
            )
            result = hook_run_tests(ctx)

        assert result.status == HookStatus.FAILURE
        assert result.remediation_prompt is not None
        assert "FAILED test_foo.py" in result.remediation_prompt

    def test_npm_test_detected(self, tmp_path):
        """When package.json exists and no Python config, uses npm test."""
        (tmp_path / "package.json").write_text("{}")
        ctx = _make_ctx(worktree=tmp_path)

        with patch("orchestrator.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = hook_run_tests(ctx)

        assert result.status == HookStatus.SUCCESS
        assert mock_run.call_args[0][0] == ["npm", "test"]

    def test_timeout(self, tmp_path):
        """Tests timing out returns FAILURE."""
        (tmp_path / "pyproject.toml").write_text("")
        ctx = _make_ctx(worktree=tmp_path)

        with patch("orchestrator.hooks.subprocess.run", side_effect=subprocess.TimeoutExpired(["pytest"], 300)):
            result = hook_run_tests(ctx)

        assert result.status == HookStatus.FAILURE
        assert "timed out" in result.message


# ---------------------------------------------------------------------------
# hook_merge_pr
# ---------------------------------------------------------------------------


class TestHookMergePr:
    """Tests for the merge_pr built-in hook."""

    def test_success(self):
        """Successful merge returns SUCCESS with pr_number in context."""
        ctx = _make_ctx(extra={"pr_number": 42, "pr_url": "https://github.com/test/pr/42"})

        with patch("orchestrator.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Merged", stderr="")
            result = hook_merge_pr(ctx)

        assert result.status == HookStatus.SUCCESS
        assert result.context["pr_number"] == 42
        assert result.context["pr_url"] == "https://github.com/test/pr/42"
        assert "42" in result.message
        # Verify the command used
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["gh", "pr", "merge", "42", "--merge"]

    def test_squash_method(self):
        """merge_method=squash passes --squash to gh."""
        ctx = _make_ctx(extra={"pr_number": 10, "merge_method": "squash"})

        with patch("orchestrator.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = hook_merge_pr(ctx)

        assert result.status == HookStatus.SUCCESS
        cmd = mock_run.call_args[0][0]
        assert "--squash" in cmd

    def test_no_pr_skips(self):
        """When no pr_number in extra, returns SKIP."""
        ctx = _make_ctx(extra={})
        result = hook_merge_pr(ctx)

        assert result.status == HookStatus.SKIP
        assert "No pr_number" in result.message

    def test_merge_failure(self):
        """Non-zero exit code returns FAILURE."""
        ctx = _make_ctx(extra={"pr_number": 99})

        with patch("orchestrator.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="PR is not mergeable"
            )
            result = hook_merge_pr(ctx)

        assert result.status == HookStatus.FAILURE
        assert "not mergeable" in result.message

    def test_timeout(self):
        """Timeout during merge returns FAILURE."""
        ctx = _make_ctx(extra={"pr_number": 7})

        with patch("orchestrator.hooks.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(["gh"], 60)):
            result = hook_merge_pr(ctx)

        assert result.status == HookStatus.FAILURE
        assert "Timeout" in result.message


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


class TestResolveHooks:
    """Tests for resolve_hooks — config loading and resolution order."""

    def test_defaults_when_no_config(self):
        """With no config, defaults to [create_pr] for before_submit."""
        with patch("orchestrator.config.get_hooks_config", return_value={}):
            hooks = resolve_hooks(HookPoint.BEFORE_SUBMIT, task_type=None)

        assert len(hooks) == 1
        assert hooks[0] is BUILTIN_HOOKS["create_pr"]

    def test_project_level_hooks(self):
        """Project-level hooks override defaults."""
        with patch("orchestrator.config.get_hooks_config", return_value={
                 "before_submit": ["run_tests", "create_pr"]
             }):
            hooks = resolve_hooks(HookPoint.BEFORE_SUBMIT, task_type=None)

        assert len(hooks) == 2
        assert hooks[0] is BUILTIN_HOOKS["run_tests"]
        assert hooks[1] is BUILTIN_HOOKS["create_pr"]

    def test_unknown_hook_name_skipped(self):
        """Unknown hook names are silently skipped."""
        with patch("orchestrator.config.get_hooks_config", return_value={
                 "before_submit": ["nonexistent_hook", "create_pr"]
             }):
            hooks = resolve_hooks(HookPoint.BEFORE_SUBMIT)

        assert len(hooks) == 1
        assert hooks[0] is BUILTIN_HOOKS["create_pr"]

    def test_empty_hook_point(self):
        """Hook points with no config and no defaults return empty list."""
        with patch("orchestrator.config.get_hooks_config", return_value={}), \
             patch("orchestrator.hooks.DEFAULT_HOOKS", {"before_submit": ["create_pr"]}):
            hooks = resolve_hooks(HookPoint.BEFORE_MERGE, task_type=None)

        assert hooks == []

    def test_before_merge_defaults(self):
        """BEFORE_MERGE defaults to [merge_pr] when no config."""
        with patch("orchestrator.config.get_hooks_config", return_value={}):
            hooks = resolve_hooks(HookPoint.BEFORE_MERGE, task_type=None)

        assert len(hooks) == 1
        assert hooks[0] is BUILTIN_HOOKS["merge_pr"]


# ---------------------------------------------------------------------------
# run_hooks
# ---------------------------------------------------------------------------


class TestRunHooks:
    """Tests for the run_hooks runner."""

    def test_empty_hooks_succeed(self):
        """No hooks configured → all_ok=True, empty results."""
        ctx = _make_ctx()

        with patch("orchestrator.hooks.resolve_hooks", return_value=[]):
            all_ok, results = run_hooks(HookPoint.BEFORE_SUBMIT, ctx)

        assert all_ok is True
        assert results == []

    def test_all_success(self):
        """All hooks succeed → all_ok=True."""
        ctx = _make_ctx()
        hook_a = MagicMock(return_value=HookResult(status=HookStatus.SUCCESS, message="ok"))
        hook_b = MagicMock(return_value=HookResult(status=HookStatus.SUCCESS, message="ok"))

        with patch("orchestrator.hooks.resolve_hooks", return_value=[hook_a, hook_b]):
            all_ok, results = run_hooks(HookPoint.BEFORE_SUBMIT, ctx)

        assert all_ok is True
        assert len(results) == 2
        hook_a.assert_called_once_with(ctx)
        hook_b.assert_called_once_with(ctx)

    def test_fail_fast(self):
        """First failure stops execution — second hook is not called."""
        ctx = _make_ctx()
        hook_a = MagicMock(return_value=HookResult(status=HookStatus.FAILURE, message="boom"))
        hook_b = MagicMock(return_value=HookResult(status=HookStatus.SUCCESS, message="ok"))

        with patch("orchestrator.hooks.resolve_hooks", return_value=[hook_a, hook_b]):
            all_ok, results = run_hooks(HookPoint.BEFORE_SUBMIT, ctx)

        assert all_ok is False
        assert len(results) == 1
        hook_b.assert_not_called()

    def test_skip_continues(self):
        """SKIP status does not stop execution."""
        ctx = _make_ctx()
        hook_a = MagicMock(return_value=HookResult(status=HookStatus.SKIP, message="skipped"))
        hook_b = MagicMock(return_value=HookResult(status=HookStatus.SUCCESS, message="ok"))

        with patch("orchestrator.hooks.resolve_hooks", return_value=[hook_a, hook_b]):
            all_ok, results = run_hooks(HookPoint.BEFORE_SUBMIT, ctx)

        assert all_ok is True
        assert len(results) == 2
        hook_b.assert_called_once()

    def test_remediation_prompt_preserved(self):
        """Failed hook with remediation_prompt is accessible in results."""
        ctx = _make_ctx()
        hook_a = MagicMock(return_value=HookResult(
            status=HookStatus.FAILURE,
            message="conflict",
            remediation_prompt="fix conflicts please",
        ))

        with patch("orchestrator.hooks.resolve_hooks", return_value=[hook_a]):
            all_ok, results = run_hooks(HookPoint.BEFORE_SUBMIT, ctx)

        assert all_ok is False
        assert results[0].remediation_prompt == "fix conflicts please"


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    """Tests for hooks config loading from .octopoid/config.yaml."""

    def test_get_hooks_config_returns_defaults_when_no_file(self):
        """When no config file exists, defaults are returned."""
        from orchestrator.config import get_hooks_config, DEFAULT_HOOKS_CONFIG

        with patch("orchestrator.config.find_parent_project", return_value=Path("/nonexistent")):
            result = get_hooks_config()

        assert result == DEFAULT_HOOKS_CONFIG

    def test_get_hooks_config_reads_yaml(self, tmp_path):
        """Reads hooks from actual yaml config."""
        from orchestrator.config import get_hooks_config

        config_dir = tmp_path / ".octopoid"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "hooks:\n  before_submit:\n    - rebase_on_main\n    - create_pr\n"
        )

        with patch("orchestrator.config.find_parent_project", return_value=tmp_path):
            result = get_hooks_config()

        assert result == {"before_submit": ["rebase_on_main", "create_pr"]}

# ---------------------------------------------------------------------------
# approve_and_merge (queue_utils integration with hooks)
# ---------------------------------------------------------------------------


class TestApproveAndMerge:
    """Tests for queue_utils.approve_and_merge using BEFORE_MERGE hooks."""

    def _mock_task(self, **overrides):
        """Return a fake task dict."""
        task = {
            "id": "TSK-100",
            "title": "Implement feature X",
            "file_path": "/tasks/TSK-100.md",
            "type": None,
            "branch_name": "task/TSK-100",
            "base_branch": "main",
            "assigned_to": "implementer-1",
            "pr_number": 55,
            "pr_url": "https://github.com/test/pr/55",
        }
        task.update(overrides)
        return task

    @patch("orchestrator.task_notes.cleanup_task_notes")
    @patch("orchestrator.hooks.run_hooks")
    def test_success_flow(self, mock_run_hooks, mock_cleanup, mock_sdk_for_unit_tests):
        """Hooks pass → task accepted via SDK."""
        mock_sdk_for_unit_tests.tasks.get.return_value = self._mock_task()

        mock_run_hooks.return_value = (True, [
            HookResult(status=HookStatus.SUCCESS, message="Merged", context={"pr_number": 55}),
        ])

        from orchestrator.queue_utils import approve_and_merge

        result = approve_and_merge("TSK-100")

        assert result["merged"] is True
        assert result.get("error") is None
        mock_sdk_for_unit_tests.tasks.accept.assert_called_once_with("TSK-100", accepted_by="scheduler")
        mock_cleanup.assert_called_once_with("TSK-100")

    @patch("orchestrator.task_notes.cleanup_task_notes")
    @patch("orchestrator.hooks.run_hooks")
    def test_hook_failure_blocks_accept(self, mock_run_hooks, mock_cleanup, mock_sdk_for_unit_tests):
        """Hook failure → task NOT accepted, error returned."""
        mock_sdk_for_unit_tests.tasks.get.return_value = self._mock_task()

        mock_run_hooks.return_value = (False, [
            HookResult(status=HookStatus.FAILURE, message="PR is not mergeable"),
        ])

        from orchestrator.queue_utils import approve_and_merge

        result = approve_and_merge("TSK-100")

        assert "error" in result
        assert "not mergeable" in result["error"]
        mock_sdk_for_unit_tests.tasks.accept.assert_not_called()
        mock_cleanup.assert_not_called()

    def test_missing_task(self, mock_sdk_for_unit_tests):
        """Non-existent task returns error."""
        mock_sdk_for_unit_tests.tasks.get.return_value = None

        from orchestrator.queue_utils import approve_and_merge

        result = approve_and_merge("TSK-MISSING")

        assert "error" in result
        assert "not found" in result["error"]

    @patch("orchestrator.task_notes.cleanup_task_notes")
    @patch("orchestrator.hooks.run_hooks")
    def test_no_pr_skips_merge_still_accepts(self, mock_run_hooks, mock_cleanup, mock_sdk_for_unit_tests):
        """Task without PR: merge_pr hook skips, task still accepted."""
        mock_sdk_for_unit_tests.tasks.get.return_value = self._mock_task(pr_number=None, pr_url=None)

        mock_run_hooks.return_value = (True, [
            HookResult(status=HookStatus.SKIP, message="No pr_number"),
        ])

        from orchestrator.queue_utils import approve_and_merge

        result = approve_and_merge("TSK-100")

        assert result.get("error") is None
        assert result["merged"] is False  # No hook set merged
        mock_sdk_for_unit_tests.tasks.accept.assert_called_once()
