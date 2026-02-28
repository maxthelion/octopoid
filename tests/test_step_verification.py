"""Tests for the three-phase Step abstraction (pre_check, execute, verify).

Tests:
1. Runner behaviour with mock Steps: pre_check skip, verify failure, error classification
2. check_done logic for each converted step (push_branch, create_pr, merge_pr, rebase_on_base)
3. Backwards compatibility: old-style functions still work in execute_steps
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from octopoid.steps import (
    PermanentStepError,
    RetryableStepError,
    Step,
    StepContext,
    StepVerificationError,
    execute_steps,
)


# =============================================================================
# Helpers / mock Step factories
# =============================================================================


def make_step(
    name: str = "test_step",
    pre_check_return: bool = False,
    execute_side_effect=None,
    verify_side_effect=None,
) -> Step:
    """Create a mock Step with controllable behaviour."""
    step = MagicMock(spec=Step)
    step.name = name
    step.pre_check.return_value = pre_check_return
    if execute_side_effect is not None:
        step.execute.side_effect = execute_side_effect
    if verify_side_effect is not None:
        step.verify.side_effect = verify_side_effect
    return step


def ctx_for(task_dir: Path, task: dict | None = None, result: dict | None = None) -> StepContext:
    return StepContext(task=task or {}, result=result or {}, task_dir=task_dir)


# =============================================================================
# Runner tests: pre_check skip
# =============================================================================


class TestRunnerPreCheckSkip:
    """Runner skips execute and verify when pre_check returns True."""

    def test_step_skipped_when_pre_check_true(self, tmp_path, mock_sdk_for_unit_tests):
        """execute_steps skips execute/verify and marks step completed when pre_check is True."""
        from octopoid.steps import STEP_REGISTRY

        step = make_step("mock_step", pre_check_return=True)
        STEP_REGISTRY["mock_step"] = step

        try:
            execute_steps(["mock_step"], {}, {}, tmp_path)
        finally:
            del STEP_REGISTRY["mock_step"]

        step.pre_check.assert_called_once()
        step.execute.assert_not_called()
        step.verify.assert_not_called()

        # step_progress.json should record the step as completed
        progress = json.loads((tmp_path / "step_progress.json").read_text())
        assert "mock_step" in progress["completed"]
        assert progress["failed"] is None

    def test_skipped_step_counted_as_completed(self, tmp_path, mock_sdk_for_unit_tests):
        """A pre_check-skipped step appears in completed, not failed."""
        from octopoid.steps import STEP_REGISTRY

        step_a = make_step("step_a", pre_check_return=True)
        step_b = make_step("step_b", pre_check_return=False)
        STEP_REGISTRY["step_a"] = step_a
        STEP_REGISTRY["step_b"] = step_b

        try:
            execute_steps(["step_a", "step_b"], {}, {}, tmp_path)
        finally:
            del STEP_REGISTRY["step_a"]
            del STEP_REGISTRY["step_b"]

        # Both steps should be completed (step_a skipped, step_b executed)
        progress = json.loads((tmp_path / "step_progress.json").read_text())
        assert "step_a" in progress["completed"]
        assert "step_b" in progress["completed"]
        assert progress["failed"] is None

    def test_subsequent_steps_run_after_skipped_step(self, tmp_path, mock_sdk_for_unit_tests):
        """Skipped step does not prevent later steps from running."""
        from octopoid.steps import STEP_REGISTRY

        call_log: list[str] = []
        step_a = make_step("step_a", pre_check_return=True)
        step_b = make_step("step_b", pre_check_return=False)
        step_b.execute.side_effect = lambda ctx: call_log.append("step_b_executed")
        STEP_REGISTRY["step_a"] = step_a
        STEP_REGISTRY["step_b"] = step_b

        try:
            execute_steps(["step_a", "step_b"], {}, {}, tmp_path)
        finally:
            del STEP_REGISTRY["step_a"]
            del STEP_REGISTRY["step_b"]

        assert "step_b_executed" in call_log


# =============================================================================
# Runner tests: verify failure
# =============================================================================


class TestRunnerVerifyFailure:
    """Runner raises StepVerificationError when verify() fails."""

    def test_verify_failure_raises_step_verification_error(self, tmp_path, mock_sdk_for_unit_tests):
        """execute_steps propagates StepVerificationError from verify()."""
        from octopoid.steps import STEP_REGISTRY

        step = make_step(
            "mock_step",
            verify_side_effect=StepVerificationError("verify failed"),
        )
        STEP_REGISTRY["mock_step"] = step

        try:
            with pytest.raises(StepVerificationError, match="verify failed"):
                execute_steps(["mock_step"], {}, {}, tmp_path)
        finally:
            del STEP_REGISTRY["mock_step"]

    def test_verify_failure_writes_step_as_failed(self, tmp_path, mock_sdk_for_unit_tests):
        """When verify raises, step_progress.json records the step as failed."""
        from octopoid.steps import STEP_REGISTRY

        step = make_step(
            "mock_step",
            verify_side_effect=StepVerificationError("verify failed"),
        )
        STEP_REGISTRY["mock_step"] = step

        try:
            with pytest.raises(StepVerificationError):
                execute_steps(["mock_step"], {}, {}, tmp_path)
        finally:
            del STEP_REGISTRY["mock_step"]

        progress = json.loads((tmp_path / "step_progress.json").read_text())
        assert progress["failed"] == "mock_step"
        assert "mock_step" not in progress["completed"]

    def test_execute_called_before_verify(self, tmp_path, mock_sdk_for_unit_tests):
        """verify is only called after execute completes successfully."""
        from octopoid.steps import STEP_REGISTRY

        call_order: list[str] = []
        step = make_step("mock_step")
        step.execute.side_effect = lambda ctx: call_order.append("execute")
        step.verify.side_effect = lambda ctx: call_order.append("verify")
        STEP_REGISTRY["mock_step"] = step

        try:
            execute_steps(["mock_step"], {}, {}, tmp_path)
        finally:
            del STEP_REGISTRY["mock_step"]

        assert call_order == ["execute", "verify"]


# =============================================================================
# Runner tests: error classification
# =============================================================================


class TestRunnerErrorClassification:
    """Runner propagates errors with correct types."""

    def test_retryable_error_propagated(self, tmp_path, mock_sdk_for_unit_tests):
        """RetryableStepError from execute() is propagated unchanged."""
        from octopoid.steps import STEP_REGISTRY

        step = make_step(
            "mock_step",
            execute_side_effect=RetryableStepError("transient failure"),
        )
        STEP_REGISTRY["mock_step"] = step

        try:
            with pytest.raises(RetryableStepError, match="transient failure"):
                execute_steps(["mock_step"], {}, {}, tmp_path)
        finally:
            del STEP_REGISTRY["mock_step"]

    def test_permanent_error_propagated(self, tmp_path, mock_sdk_for_unit_tests):
        """PermanentStepError from execute() is propagated unchanged."""
        from octopoid.steps import STEP_REGISTRY

        step = make_step(
            "mock_step",
            execute_side_effect=PermanentStepError("needs intervention"),
        )
        STEP_REGISTRY["mock_step"] = step

        try:
            with pytest.raises(PermanentStepError, match="needs intervention"):
                execute_steps(["mock_step"], {}, {}, tmp_path)
        finally:
            del STEP_REGISTRY["mock_step"]

    def test_generic_exception_propagated(self, tmp_path, mock_sdk_for_unit_tests):
        """Generic RuntimeError from execute() is propagated unchanged."""
        from octopoid.steps import STEP_REGISTRY

        step = make_step(
            "mock_step",
            execute_side_effect=RuntimeError("unexpected error"),
        )
        STEP_REGISTRY["mock_step"] = step

        try:
            with pytest.raises(RuntimeError, match="unexpected error"):
                execute_steps(["mock_step"], {}, {}, tmp_path)
        finally:
            del STEP_REGISTRY["mock_step"]

    def test_error_writes_step_as_failed(self, tmp_path, mock_sdk_for_unit_tests):
        """Any exception from execute() marks the step as failed in step_progress.json."""
        from octopoid.steps import STEP_REGISTRY

        step = make_step(
            "mock_step",
            execute_side_effect=RuntimeError("bang"),
        )
        STEP_REGISTRY["mock_step"] = step

        try:
            with pytest.raises(RuntimeError):
                execute_steps(["mock_step"], {}, {}, tmp_path)
        finally:
            del STEP_REGISTRY["mock_step"]

        progress = json.loads((tmp_path / "step_progress.json").read_text())
        assert progress["failed"] == "mock_step"

    def test_previous_completed_steps_preserved_on_failure(self, tmp_path, mock_sdk_for_unit_tests):
        """Completed steps are preserved in step_progress.json when a later step fails."""
        from octopoid.steps import STEP_REGISTRY

        step_ok = make_step("step_ok")
        step_bad = make_step("step_bad", execute_side_effect=RuntimeError("step_bad failed"))
        STEP_REGISTRY["step_ok"] = step_ok
        STEP_REGISTRY["step_bad"] = step_bad

        try:
            with pytest.raises(RuntimeError):
                execute_steps(["step_ok", "step_bad"], {}, {}, tmp_path)
        finally:
            del STEP_REGISTRY["step_ok"]
            del STEP_REGISTRY["step_bad"]

        progress = json.loads((tmp_path / "step_progress.json").read_text())
        assert "step_ok" in progress["completed"]
        assert progress["failed"] == "step_bad"

    def test_old_style_function_propagates_retryable_error(self, tmp_path, mock_sdk_for_unit_tests):
        """RetryableStepError from an old-style step function is propagated."""
        from octopoid.steps import STEP_REGISTRY

        def failing_fn(task, result, task_dir):
            raise RetryableStepError("old style retryable")

        STEP_REGISTRY["old_step"] = failing_fn

        try:
            with pytest.raises(RetryableStepError, match="old style retryable"):
                execute_steps(["old_step"], {}, {}, tmp_path)
        finally:
            del STEP_REGISTRY["old_step"]


# =============================================================================
# Backwards compatibility: old-style functions
# =============================================================================


class TestOldStyleFunctionBackwardsCompat:
    """execute_steps still works with old-style (task, result, task_dir) functions."""

    def test_old_style_function_is_called_with_task_result_task_dir(self, tmp_path, mock_sdk_for_unit_tests):
        """Old-style function receives task, result, task_dir positional arguments."""
        from octopoid.steps import STEP_REGISTRY

        received: list = []

        def capture_fn(task, result, task_dir):
            received.extend([task, result, task_dir])

        STEP_REGISTRY["capture_step"] = capture_fn
        task = {"id": "abc"}
        result_dict = {"outcome": "done"}

        try:
            execute_steps(["capture_step"], task, result_dict, tmp_path)
        finally:
            del STEP_REGISTRY["capture_step"]

        assert received[0] is task
        assert received[1] is result_dict
        assert received[2] is tmp_path

    def test_old_style_function_marked_completed(self, tmp_path, mock_sdk_for_unit_tests):
        """Successful old-style function marks step as completed in step_progress."""
        from octopoid.steps import STEP_REGISTRY

        STEP_REGISTRY["noop_step"] = lambda t, r, d: None

        try:
            execute_steps(["noop_step"], {}, {}, tmp_path)
        finally:
            del STEP_REGISTRY["noop_step"]

        progress = json.loads((tmp_path / "step_progress.json").read_text())
        assert "noop_step" in progress["completed"]
        assert progress["failed"] is None


# =============================================================================
# Step.check_done() tests: push_branch
# =============================================================================


class TestPushBranchCheckDone:
    """Tests for _PushBranchStep.check_done()."""

    def _make_ctx(self, tmp_path: Path, task: dict | None = None) -> StepContext:
        task_dir = tmp_path
        worktree = task_dir / "worktree"
        worktree.mkdir(exist_ok=True)
        return StepContext(task=task or {"id": "abc123"}, result={}, task_dir=task_dir)

    def test_check_done_true_when_branch_exists_on_remote(self, tmp_path):
        """check_done returns True when git ls-remote exits 0 (branch found)."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["push_branch"]
        ctx = self._make_ctx(tmp_path)

        ok_result = MagicMock(returncode=0, stdout="abc123 refs/heads/agent/abc123\n")
        with patch("octopoid.steps.subprocess.run", return_value=ok_result), \
             patch("octopoid.git_utils.get_task_branch", return_value="agent/abc123"):
            assert step.check_done(ctx) is True

    def test_check_done_false_when_branch_missing(self, tmp_path):
        """check_done returns False when git ls-remote exits 2 (branch not found)."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["push_branch"]
        ctx = self._make_ctx(tmp_path)

        not_found = MagicMock(returncode=2, stdout="")
        with patch("octopoid.steps.subprocess.run", return_value=not_found), \
             patch("octopoid.git_utils.get_task_branch", return_value="agent/abc123"):
            assert step.check_done(ctx) is False

    def test_verify_raises_when_branch_not_on_remote(self, tmp_path):
        """verify() raises StepVerificationError when branch is absent from remote."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["push_branch"]
        ctx = self._make_ctx(tmp_path)

        not_found = MagicMock(returncode=2, stdout="")
        with patch("octopoid.steps.subprocess.run", return_value=not_found), \
             patch("octopoid.git_utils.get_task_branch", return_value="agent/abc123"):
            with pytest.raises(StepVerificationError, match="push_branch verify failed"):
                step.verify(ctx)

    def test_verify_passes_when_branch_exists(self, tmp_path):
        """verify() does not raise when branch exists on remote."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["push_branch"]
        ctx = self._make_ctx(tmp_path)

        ok_result = MagicMock(returncode=0, stdout="abc123 refs/heads/agent/abc123\n")
        with patch("octopoid.steps.subprocess.run", return_value=ok_result), \
             patch("octopoid.git_utils.get_task_branch", return_value="agent/abc123"):
            # Should not raise
            step.verify(ctx)

    def test_pre_check_skips_when_branch_exists(self, tmp_path):
        """pre_check delegates to check_done — returns True when branch is remote."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["push_branch"]
        ctx = self._make_ctx(tmp_path)

        ok_result = MagicMock(returncode=0, stdout="abc123 refs/heads/agent/abc123\n")
        with patch("octopoid.steps.subprocess.run", return_value=ok_result), \
             patch("octopoid.git_utils.get_task_branch", return_value="agent/abc123"):
            assert step.pre_check(ctx) is True


# =============================================================================
# Step.check_done() tests: create_pr
# =============================================================================


class TestCreatePrCheckDone:
    """Tests for _CreatePrStep.check_done()."""

    def _make_ctx(self, tmp_path: Path) -> StepContext:
        task_dir = tmp_path
        (task_dir / "worktree").mkdir(exist_ok=True)
        return StepContext(task={"id": "abc123"}, result={}, task_dir=task_dir)

    def test_check_done_true_when_pr_exists(self, tmp_path):
        """check_done returns True when gh pr view exits 0 with a number."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["create_pr"]
        ctx = self._make_ctx(tmp_path)

        ok = MagicMock(returncode=0, stdout=json.dumps({"number": 42}))
        with patch("octopoid.steps.subprocess.run", return_value=ok), \
             patch("octopoid.git_utils.get_task_branch", return_value="agent/abc123"):
            assert step.check_done(ctx) is True

    def test_check_done_false_when_no_pr(self, tmp_path):
        """check_done returns False when gh pr view fails (no PR for branch)."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["create_pr"]
        ctx = self._make_ctx(tmp_path)

        not_found = MagicMock(returncode=1, stdout="")
        with patch("octopoid.steps.subprocess.run", return_value=not_found), \
             patch("octopoid.git_utils.get_task_branch", return_value="agent/abc123"):
            assert step.check_done(ctx) is False

    def test_check_done_false_when_json_has_no_number(self, tmp_path):
        """check_done returns False when response JSON has no 'number' field."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["create_pr"]
        ctx = self._make_ctx(tmp_path)

        ok = MagicMock(returncode=0, stdout=json.dumps({}))
        with patch("octopoid.steps.subprocess.run", return_value=ok), \
             patch("octopoid.git_utils.get_task_branch", return_value="agent/abc123"):
            assert step.check_done(ctx) is False

    def test_verify_raises_when_pr_not_found(self, tmp_path, mock_sdk_for_unit_tests):
        """verify() raises StepVerificationError when check_done returns False."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["create_pr"]
        ctx = self._make_ctx(tmp_path)

        not_found = MagicMock(returncode=1, stdout="")
        with patch("octopoid.steps.subprocess.run", return_value=not_found), \
             patch("octopoid.git_utils.get_task_branch", return_value="agent/abc123"):
            with pytest.raises(StepVerificationError, match="create_pr verify failed"):
                step.verify(ctx)

    def test_verify_raises_when_pr_number_not_on_task(self, tmp_path, mock_sdk_for_unit_tests):
        """verify() raises StepVerificationError when pr_number not stored on task."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["create_pr"]
        ctx = self._make_ctx(tmp_path)

        # PR exists on GitHub
        ok = MagicMock(returncode=0, stdout=json.dumps({"number": 42}))
        # But SDK says task has no pr_number
        mock_sdk_for_unit_tests.tasks.get.return_value = {"id": "abc123", "queue": "claimed"}

        with patch("octopoid.steps.subprocess.run", return_value=ok), \
             patch("octopoid.git_utils.get_task_branch", return_value="agent/abc123"), \
             patch("octopoid.sdk.get_sdk", return_value=mock_sdk_for_unit_tests):
            with pytest.raises(StepVerificationError, match="pr_number not stored"):
                step.verify(ctx)

    def test_pre_check_stores_pr_metadata_and_returns_true(self, tmp_path, mock_sdk_for_unit_tests):
        """pre_check stores pr_number and pr_url on task when PR already exists."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["create_pr"]
        ctx = self._make_ctx(tmp_path)

        pr_data = {"number": 42, "url": "https://github.com/test/repo/pull/42"}
        ok = MagicMock(returncode=0, stdout=json.dumps(pr_data))

        with patch("octopoid.steps.subprocess.run", return_value=ok), \
             patch("octopoid.git_utils.get_task_branch", return_value="agent/abc123"), \
             patch("octopoid.sdk.get_sdk", return_value=mock_sdk_for_unit_tests):
            result = step.pre_check(ctx)

        assert result is True
        mock_sdk_for_unit_tests.tasks.update.assert_called_once_with(
            "abc123",
            pr_url="https://github.com/test/repo/pull/42",
            pr_number=42,
        )

    def test_pre_check_returns_false_when_no_pr(self, tmp_path, mock_sdk_for_unit_tests):
        """pre_check returns False when no PR exists for the branch."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["create_pr"]
        ctx = self._make_ctx(tmp_path)

        not_found = MagicMock(returncode=1, stdout="")
        with patch("octopoid.steps.subprocess.run", return_value=not_found), \
             patch("octopoid.git_utils.get_task_branch", return_value="agent/abc123"):
            result = step.pre_check(ctx)

        assert result is False
        mock_sdk_for_unit_tests.tasks.update.assert_not_called()


# =============================================================================
# Step.check_done() tests: merge_pr
# =============================================================================


class TestMergePrCheckDone:
    """Tests for _MergePrStep.check_done()."""

    def _make_ctx(self, tmp_path: Path, pr_number: int | None = 42) -> StepContext:
        task = {"id": "abc123"}
        if pr_number is not None:
            task["pr_number"] = pr_number
        return StepContext(task=task, result={}, task_dir=tmp_path)

    def test_check_done_true_when_pr_merged(self, tmp_path):
        """check_done returns True when PR state is MERGED."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["merge_pr"]
        ctx = self._make_ctx(tmp_path)

        ok = MagicMock(returncode=0, stdout="MERGED\n")
        with patch("octopoid.steps.subprocess.run", return_value=ok):
            assert step.check_done(ctx) is True

    def test_check_done_false_when_pr_open(self, tmp_path):
        """check_done returns False when PR state is OPEN."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["merge_pr"]
        ctx = self._make_ctx(tmp_path)

        ok = MagicMock(returncode=0, stdout="OPEN\n")
        with patch("octopoid.steps.subprocess.run", return_value=ok):
            assert step.check_done(ctx) is False

    def test_check_done_false_when_no_pr_number(self, tmp_path):
        """check_done returns False when task has no pr_number."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["merge_pr"]
        ctx = self._make_ctx(tmp_path, pr_number=None)

        with patch("octopoid.steps.subprocess.run") as mock_run:
            assert step.check_done(ctx) is False
            mock_run.assert_not_called()

    def test_check_done_false_when_gh_fails(self, tmp_path):
        """check_done returns False when gh pr view exits non-zero."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["merge_pr"]
        ctx = self._make_ctx(tmp_path)

        fail = MagicMock(returncode=1, stdout="")
        with patch("octopoid.steps.subprocess.run", return_value=fail):
            assert step.check_done(ctx) is False

    def test_verify_raises_when_pr_not_merged(self, tmp_path):
        """verify() raises StepVerificationError when PR is not in MERGED state."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["merge_pr"]
        ctx = self._make_ctx(tmp_path)

        open_result = MagicMock(returncode=0, stdout="OPEN\n")
        with patch("octopoid.steps.subprocess.run", return_value=open_result):
            with pytest.raises(StepVerificationError, match="merge_pr verify failed"):
                step.verify(ctx)

    def test_verify_passes_when_pr_merged(self, tmp_path):
        """verify() does not raise when PR is in MERGED state."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["merge_pr"]
        ctx = self._make_ctx(tmp_path)

        merged = MagicMock(returncode=0, stdout="MERGED\n")
        with patch("octopoid.steps.subprocess.run", return_value=merged):
            step.verify(ctx)  # Should not raise

    def test_pre_check_skips_when_already_merged(self, tmp_path):
        """pre_check returns True when PR is already merged (ghost completion scenario)."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["merge_pr"]
        ctx = self._make_ctx(tmp_path)

        merged = MagicMock(returncode=0, stdout="MERGED\n")
        with patch("octopoid.steps.subprocess.run", return_value=merged):
            assert step.pre_check(ctx) is True


# =============================================================================
# Step.check_done() tests: rebase_on_base
# =============================================================================


class TestRebaseOnBaseCheckDone:
    """Tests for _RebaseOnBaseStep.check_done()."""

    def _make_ctx(self, tmp_path: Path) -> StepContext:
        (tmp_path / "worktree").mkdir(exist_ok=True)
        return StepContext(task={}, result={}, task_dir=tmp_path)

    def test_check_done_true_when_already_ancestor(self, tmp_path):
        """check_done returns True when HEAD is already a descendant of origin/base."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["rebase_on_base"]
        ctx = self._make_ctx(tmp_path)

        ok = MagicMock(returncode=0)
        with patch("octopoid.steps.subprocess.run", return_value=ok), \
             patch("octopoid.config.get_base_branch", return_value="main"):
            assert step.check_done(ctx) is True

    def test_check_done_false_when_not_ancestor(self, tmp_path):
        """check_done returns False when HEAD is not a descendant of origin/base."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["rebase_on_base"]
        ctx = self._make_ctx(tmp_path)

        fail = MagicMock(returncode=1)
        with patch("octopoid.steps.subprocess.run", return_value=fail), \
             patch("octopoid.config.get_base_branch", return_value="main"):
            assert step.check_done(ctx) is False

    def test_verify_raises_when_not_ancestor(self, tmp_path):
        """verify() raises StepVerificationError when HEAD is not descendant of base."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["rebase_on_base"]
        ctx = self._make_ctx(tmp_path)

        fail = MagicMock(returncode=1)
        with patch("octopoid.steps.subprocess.run", return_value=fail), \
             patch("octopoid.config.get_base_branch", return_value="main"):
            with pytest.raises(StepVerificationError, match="rebase_on_base verify failed"):
                step.verify(ctx)

    def test_verify_passes_when_ancestor(self, tmp_path):
        """verify() does not raise when HEAD is already a descendant of base."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["rebase_on_base"]
        ctx = self._make_ctx(tmp_path)

        ok = MagicMock(returncode=0)
        with patch("octopoid.steps.subprocess.run", return_value=ok), \
             patch("octopoid.config.get_base_branch", return_value="main"):
            step.verify(ctx)  # Should not raise

    def test_pre_check_fetches_before_checking(self, tmp_path):
        """pre_check calls git fetch before checking is-ancestor."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["rebase_on_base"]
        ctx = self._make_ctx(tmp_path)

        calls: list = []

        def mock_run(cmd, *args, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0)

        with patch("octopoid.steps.subprocess.run", side_effect=mock_run), \
             patch("octopoid.config.get_base_branch", return_value="main"):
            step.pre_check(ctx)

        # First call should be git fetch
        assert calls[0][:2] == ["git", "fetch"]
        # Second call should be merge-base check
        assert "merge-base" in " ".join(calls[1])

    def test_pre_check_skips_when_already_rebased(self, tmp_path):
        """pre_check returns True when HEAD is already a descendant of origin/base."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["rebase_on_base"]
        ctx = self._make_ctx(tmp_path)

        ok = MagicMock(returncode=0)
        with patch("octopoid.steps.subprocess.run", return_value=ok), \
             patch("octopoid.config.get_base_branch", return_value="main"):
            assert step.pre_check(ctx) is True


# =============================================================================
# Step callable API
# =============================================================================


class TestStepCallableApi:
    """Step instances support old-style function call API."""

    def test_step_callable_invokes_execute(self, tmp_path, mock_sdk_for_unit_tests):
        """Calling a Step instance like a function calls execute() internally."""
        executed: list[bool] = []

        class RecordingStep(Step):
            def execute(self, ctx: StepContext) -> None:
                executed.append(True)

        step = RecordingStep()
        step.name = "recording_step"

        # Old-style function call API
        step({}, {}, tmp_path)

        assert executed == [True]

    def test_registered_step_callable(self, tmp_path, mock_sdk_for_unit_tests):
        """Built-in Step instances (push_branch, etc.) are callable as functions."""
        from octopoid.steps import STEP_REGISTRY

        # push_branch should be a Step instance that is also callable
        push = STEP_REGISTRY["push_branch"]
        assert isinstance(push, Step)
        assert callable(push)

    def test_module_level_names_are_step_instances(self):
        """Module-level names (push_branch, merge_pr, etc.) are Step instances."""
        from octopoid import steps

        for name in ("push_branch", "create_pr", "merge_pr", "rebase_on_base"):
            obj = getattr(steps, name)
            assert isinstance(obj, Step), f"{name} should be a Step instance"


# =============================================================================
# Error type hierarchy
# =============================================================================


class TestErrorTypeHierarchy:
    """Verify error type inheritance relationships."""

    def test_retryable_step_error_is_runtime_error(self):
        assert issubclass(RetryableStepError, RuntimeError)

    def test_step_verification_error_is_runtime_error(self):
        assert issubclass(StepVerificationError, RuntimeError)

    def test_permanent_step_error_is_runtime_error(self):
        assert issubclass(PermanentStepError, RuntimeError)

    def test_retryable_not_subclass_of_step_verification(self):
        assert not issubclass(RetryableStepError, StepVerificationError)

    def test_step_verification_not_subclass_of_retryable(self):
        assert not issubclass(StepVerificationError, RetryableStepError)


# =============================================================================
# Step base class defaults
# =============================================================================


class TestStepBaseClassDefaults:
    """Step base class has sensible defaults."""

    def test_check_done_returns_false_by_default(self, tmp_path):
        """Base Step.check_done() always returns False."""
        class MyStep(Step):
            def execute(self, ctx):
                pass

        s = MyStep()
        ctx = StepContext(task={}, result={}, task_dir=tmp_path)
        assert s.check_done(ctx) is False

    def test_pre_check_delegates_to_check_done(self, tmp_path):
        """Base Step.pre_check() returns check_done() result."""
        class MyStep(Step):
            def execute(self, ctx):
                pass
            def check_done(self, ctx):
                return True

        s = MyStep()
        ctx = StepContext(task={}, result={}, task_dir=tmp_path)
        assert s.pre_check(ctx) is True

    def test_verify_is_noop_by_default(self, tmp_path):
        """Base Step.verify() does not raise."""
        class MyStep(Step):
            def execute(self, ctx):
                pass

        s = MyStep()
        ctx = StepContext(task={}, result={}, task_dir=tmp_path)
        s.verify(ctx)  # Should not raise

    def test_execute_raises_not_implemented_on_bare_step(self, tmp_path):
        """Bare Step.execute() raises NotImplementedError."""
        # Cannot instantiate Step directly (abstract method), use a subclass
        # that does NOT override execute to confirm the base raises.
        class BareStep(Step):
            pass

        s = BareStep()
        s.name = "bare"
        ctx = StepContext(task={}, result={}, task_dir=tmp_path)
        with pytest.raises(NotImplementedError, match="must implement execute"):
            s.execute(ctx)


# =============================================================================
# rebase_on_base execute phase
# =============================================================================


class TestRebaseOnBaseExecute:
    """Tests for _RebaseOnBaseStep.execute()."""

    def _make_ctx(self, tmp_path: Path) -> StepContext:
        (tmp_path / "worktree").mkdir(exist_ok=True)
        return StepContext(task={}, result={}, task_dir=tmp_path)

    def test_execute_success(self, tmp_path):
        """execute() performs git fetch and rebase successfully."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["rebase_on_base"]
        ctx = self._make_ctx(tmp_path)

        ok = MagicMock(returncode=0, stdout="", stderr="")

        with patch("octopoid.steps.subprocess.run", return_value=ok), \
             patch("octopoid.config.get_base_branch", return_value="main"):
            step.execute(ctx)  # Should not raise

    def test_execute_raises_when_fetch_fails(self, tmp_path):
        """execute() raises RuntimeError when git fetch fails."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["rebase_on_base"]
        ctx = self._make_ctx(tmp_path)

        fetch_fail = MagicMock(returncode=1, stdout="", stderr="fatal: connection refused")
        ok = MagicMock(returncode=0, stdout="", stderr="")

        def mock_run(cmd, *args, **kwargs):
            if "fetch" in cmd:
                return fetch_fail
            return ok

        with patch("octopoid.steps.subprocess.run", side_effect=mock_run), \
             patch("octopoid.config.get_base_branch", return_value="main"):
            with pytest.raises(RuntimeError, match="git fetch failed"):
                step.execute(ctx)

    def test_execute_raises_when_rebase_fails(self, tmp_path):
        """execute() raises RuntimeError and aborts rebase when git rebase fails."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["rebase_on_base"]
        ctx = self._make_ctx(tmp_path)

        ok = MagicMock(returncode=0, stdout="", stderr="")
        rebase_fail = MagicMock(returncode=1, stdout="conflict", stderr="CONFLICT")

        calls: list = []

        def mock_run(cmd, *args, **kwargs):
            calls.append(cmd)
            if "rebase" in cmd and "--abort" not in cmd:
                return rebase_fail
            return ok

        with patch("octopoid.steps.subprocess.run", side_effect=mock_run), \
             patch("octopoid.config.get_base_branch", return_value="main"):
            with pytest.raises(RuntimeError, match="git rebase"):
                step.execute(ctx)

        # Verify that git rebase --abort was called after failure
        abort_calls = [c for c in calls if "rebase" in c and "--abort" in c]
        assert len(abort_calls) == 1


# =============================================================================
# push_branch execute phase
# =============================================================================


class TestPushBranchExecute:
    """Tests for _PushBranchStep.execute()."""

    def _make_ctx(self, tmp_path: Path, task: dict | None = None) -> StepContext:
        (tmp_path / "worktree").mkdir(exist_ok=True)
        return StepContext(task=task or {"id": "abc123"}, result={}, task_dir=tmp_path)

    def test_execute_calls_repo_manager(self, tmp_path, mock_sdk_for_unit_tests):
        """execute() calls RepoManager.ensure_on_branch and push_branch."""
        from octopoid.steps import STEP_REGISTRY
        step = STEP_REGISTRY["push_branch"]
        ctx = self._make_ctx(tmp_path)

        mock_repo = MagicMock()
        mock_repo_cls = MagicMock(return_value=mock_repo)

        with patch("octopoid.repo_manager.RepoManager", mock_repo_cls), \
             patch("octopoid.git_utils.get_task_branch", return_value="agent/abc123"):
            step.execute(ctx)

        mock_repo.ensure_on_branch.assert_called_once_with("agent/abc123")
        mock_repo.push_branch.assert_called_once()
