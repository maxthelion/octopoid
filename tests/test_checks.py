"""Tests for octopoid/checks.py — async check registry and check_ci."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestCheckRegistry:
    """Tests for the check registry itself."""

    def test_check_ci_registered(self):
        """check_ci is registered in the CHECK_REGISTRY."""
        from octopoid.checks import CHECK_REGISTRY

        assert "check_ci" in CHECK_REGISTRY

    def test_register_check_decorator(self):
        """register_check decorator adds function to CHECK_REGISTRY."""
        from octopoid.checks import CHECK_REGISTRY, CheckResult, register_check

        @register_check("_test_reg_check")
        def _my_check(task: dict) -> CheckResult:
            return CheckResult.PASS

        assert "_test_reg_check" in CHECK_REGISTRY
        # Cleanup
        del CHECK_REGISTRY["_test_reg_check"]


class TestCheckResult:
    """Tests for the CheckResult enum."""

    def test_enum_values(self):
        """CheckResult has the expected members."""
        from octopoid.checks import CheckResult

        assert CheckResult.PASS.value == "pass"
        assert CheckResult.FAIL.value == "fail"
        assert CheckResult.PENDING.value == "pending"


class TestEvaluateChecks:
    """Tests for the evaluate_checks aggregation function."""

    def test_empty_check_list_returns_pass(self):
        """No checks → aggregate result is PASS."""
        from octopoid.checks import CheckResult, evaluate_checks

        result, reason = evaluate_checks([], {})
        assert result == CheckResult.PASS
        assert reason == ""

    def test_all_pass_returns_pass(self):
        """All checks passing → aggregate result is PASS."""
        from octopoid.checks import CHECK_REGISTRY, CheckResult, evaluate_checks

        CHECK_REGISTRY["_tp"] = lambda task: CheckResult.PASS
        try:
            result, reason = evaluate_checks(["_tp", "_tp"], {})
            assert result == CheckResult.PASS
        finally:
            del CHECK_REGISTRY["_tp"]

    def test_any_fail_returns_fail(self):
        """Any check failing → aggregate result is FAIL."""
        from octopoid.checks import CHECK_REGISTRY, CheckResult, evaluate_checks

        CHECK_REGISTRY["_tpass"] = lambda task: CheckResult.PASS
        CHECK_REGISTRY["_tfail"] = lambda task: CheckResult.FAIL
        try:
            result, reason = evaluate_checks(["_tpass", "_tfail"], {})
            assert result == CheckResult.FAIL
            assert reason != ""
        finally:
            del CHECK_REGISTRY["_tpass"]
            del CHECK_REGISTRY["_tfail"]

    def test_fail_takes_precedence_over_pending(self):
        """FAIL takes precedence over PENDING even if pending comes first."""
        from octopoid.checks import CHECK_REGISTRY, CheckResult, evaluate_checks

        CHECK_REGISTRY["_tpend"] = lambda task: CheckResult.PENDING
        CHECK_REGISTRY["_tfail"] = lambda task: CheckResult.FAIL
        try:
            result, _ = evaluate_checks(["_tpend", "_tfail"], {})
            assert result == CheckResult.FAIL
        finally:
            del CHECK_REGISTRY["_tpend"]
            del CHECK_REGISTRY["_tfail"]

    def test_pending_without_fail_returns_pending(self):
        """PENDING (with no FAIL) → aggregate result is PENDING."""
        from octopoid.checks import CHECK_REGISTRY, CheckResult, evaluate_checks

        CHECK_REGISTRY["_tpass"] = lambda task: CheckResult.PASS
        CHECK_REGISTRY["_tpend"] = lambda task: CheckResult.PENDING
        try:
            result, reason = evaluate_checks(["_tpass", "_tpend"], {})
            assert result == CheckResult.PENDING
            assert reason != ""
        finally:
            del CHECK_REGISTRY["_tpass"]
            del CHECK_REGISTRY["_tpend"]

    def test_unknown_check_returns_fail(self):
        """An unknown check name → FAIL with a descriptive reason."""
        from octopoid.checks import CheckResult, evaluate_checks

        result, reason = evaluate_checks(["nonexistent_check_xyz"], {})
        assert result == CheckResult.FAIL
        assert "Unknown check" in reason

    def test_check_that_raises_returns_fail(self):
        """A check that throws unexpectedly → FAIL (not crash)."""
        from octopoid.checks import CHECK_REGISTRY, CheckResult, evaluate_checks

        def _bad_check(task: dict):
            raise RuntimeError("unexpected error")

        CHECK_REGISTRY["_tbad"] = _bad_check
        try:
            result, reason = evaluate_checks(["_tbad"], {})
            assert result == CheckResult.FAIL
            assert "raised" in reason
        finally:
            del CHECK_REGISTRY["_tbad"]


class TestCheckCi:
    """Tests for the check_ci check function."""

    def test_no_pr_returns_pass(self):
        """check_ci returns PASS when the task has no pr_number."""
        from octopoid.checks import CheckResult, check_ci

        with patch("octopoid.checks.subprocess.run") as mock_run:
            result = check_ci({})
            assert result == CheckResult.PASS
            mock_run.assert_not_called()

    def test_pr_number_none_returns_pass(self):
        """check_ci returns PASS when pr_number is None."""
        from octopoid.checks import CheckResult, check_ci

        with patch("octopoid.checks.subprocess.run") as mock_run:
            result = check_ci({"pr_number": None})
            assert result == CheckResult.PASS
            mock_run.assert_not_called()

    def test_pending_check_returns_pending(self):
        """check_ci returns PENDING when a CI check is in progress."""
        from octopoid.checks import CheckResult, check_ci

        checks = [{"name": "test-suite", "state": "IN_PROGRESS", "conclusion": None}]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(checks)
        mock_proc.stderr = ""

        with patch("octopoid.checks.subprocess.run", return_value=mock_proc):
            result = check_ci({"pr_number": 42})
        assert result == CheckResult.PENDING

    def test_queued_check_returns_pending(self):
        """check_ci returns PENDING when a CI check is queued."""
        from octopoid.checks import CheckResult, check_ci

        checks = [{"name": "build", "state": "QUEUED", "conclusion": None}]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(checks)
        mock_proc.stderr = ""

        with patch("octopoid.checks.subprocess.run", return_value=mock_proc):
            result = check_ci({"pr_number": 99})
        assert result == CheckResult.PENDING

    def test_failed_conclusion_returns_fail(self):
        """check_ci returns FAIL when a CI check has a failure conclusion."""
        from octopoid.checks import CheckResult, check_ci

        checks = [{"name": "lint", "state": "COMPLETED", "conclusion": "FAILURE"}]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(checks)
        mock_proc.stderr = ""

        with patch("octopoid.checks.subprocess.run", return_value=mock_proc):
            result = check_ci({"pr_number": 42})
        assert result == CheckResult.FAIL

    def test_all_pass_returns_pass(self):
        """check_ci returns PASS when all CI checks have passed."""
        from octopoid.checks import CheckResult, check_ci

        checks = [
            {"name": "unit-tests", "state": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "lint", "state": "COMPLETED", "conclusion": "SUCCESS"},
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(checks)
        mock_proc.stderr = ""

        with patch("octopoid.checks.subprocess.run", return_value=mock_proc):
            result = check_ci({"pr_number": 42})
        assert result == CheckResult.PASS

    def test_skipped_and_neutral_pass(self):
        """check_ci treats SKIPPED and NEUTRAL conclusions as passing."""
        from octopoid.checks import CheckResult, check_ci

        checks = [
            {"name": "optional-check", "state": "COMPLETED", "conclusion": "SKIPPED"},
            {"name": "docs-check", "state": "COMPLETED", "conclusion": "NEUTRAL"},
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(checks)
        mock_proc.stderr = ""

        with patch("octopoid.checks.subprocess.run", return_value=mock_proc):
            result = check_ci({"pr_number": 42})
        assert result == CheckResult.PASS

    def test_no_checks_configured_returns_pass(self):
        """check_ci returns PASS when the PR has no CI checks."""
        from octopoid.checks import CheckResult, check_ci

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps([])
        mock_proc.stderr = ""

        with patch("octopoid.checks.subprocess.run", return_value=mock_proc):
            result = check_ci({"pr_number": 42})
        assert result == CheckResult.PASS

    def test_failed_and_pending_returns_fail(self):
        """check_ci returns FAIL (not PENDING) when some checks failed and others pending."""
        from octopoid.checks import CheckResult, check_ci

        checks = [
            {"name": "lint", "state": "COMPLETED", "conclusion": "FAILURE"},
            {"name": "tests", "state": "IN_PROGRESS", "conclusion": None},
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(checks)
        mock_proc.stderr = ""

        with patch("octopoid.checks.subprocess.run", return_value=mock_proc):
            result = check_ci({"pr_number": 42})
        assert result == CheckResult.FAIL

    def test_timeout_returns_pending(self):
        """check_ci returns PENDING when gh times out."""
        import subprocess as _subprocess

        from octopoid.checks import CheckResult, check_ci

        with patch("octopoid.checks.subprocess.run", side_effect=_subprocess.TimeoutExpired("gh", 60)):
            result = check_ci({"pr_number": 42})
        assert result == CheckResult.PENDING

    def test_gh_not_found_returns_pass(self):
        """check_ci returns PASS when gh CLI is not installed."""
        from octopoid.checks import CheckResult, check_ci

        with patch("octopoid.checks.subprocess.run", side_effect=FileNotFoundError):
            result = check_ci({"pr_number": 42})
        assert result == CheckResult.PASS

    def test_no_stdout_returns_pass(self):
        """check_ci returns PASS when gh exits non-zero but produces no stdout (no CI configured)."""
        from octopoid.checks import CheckResult, check_ci

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "error: not found"

        with patch("octopoid.checks.subprocess.run", return_value=mock_proc):
            result = check_ci({"pr_number": 42})
        assert result == CheckResult.PASS
