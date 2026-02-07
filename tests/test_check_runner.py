"""Tests for the check_runner role and check_results DB functions."""

import json
import os
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# DB: check_results functions
# ---------------------------------------------------------------------------


class TestCheckResultsDB:
    """Tests for check_results DB column and helper functions."""

    def test_check_results_column_exists(self, initialized_db):
        """Schema v6 creates check_results column on tasks table."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import get_connection

            with get_connection() as conn:
                cursor = conn.execute("PRAGMA table_info(tasks)")
                columns = [row["name"] for row in cursor.fetchall()]
                assert "check_results" in columns

    def test_record_check_result_pass(self, initialized_db):
        """record_check_result stores a passing result."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, get_task

            create_task(
                task_id="cr1",
                file_path="/cr1.md",
                checks=["pytest-submodule"],
            )

            result = record_check_result("cr1", "pytest-submodule", "pass", "All tests passed")

            assert result is not None
            assert result["check_results"]["pytest-submodule"]["status"] == "pass"
            assert result["check_results"]["pytest-submodule"]["summary"] == "All tests passed"
            assert "timestamp" in result["check_results"]["pytest-submodule"]

    def test_record_check_result_fail(self, initialized_db):
        """record_check_result stores a failing result."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, get_task

            create_task(task_id="cr2", file_path="/cr2.md", checks=["pytest-submodule"])

            result = record_check_result("cr2", "pytest-submodule", "fail", "3 tests failed")

            assert result["check_results"]["pytest-submodule"]["status"] == "fail"
            assert "3 tests failed" in result["check_results"]["pytest-submodule"]["summary"]

    def test_record_check_result_nonexistent_task(self, initialized_db):
        """record_check_result returns None for missing task."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import record_check_result

            result = record_check_result("nonexistent", "pytest-submodule", "pass")
            assert result is None

    def test_record_multiple_check_results(self, initialized_db):
        """Multiple checks can be recorded on the same task."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result

            create_task(
                task_id="cr3",
                file_path="/cr3.md",
                checks=["pytest-submodule", "vitest"],
            )

            record_check_result("cr3", "pytest-submodule", "pass", "OK")
            result = record_check_result("cr3", "vitest", "fail", "2 failures")

            assert result["check_results"]["pytest-submodule"]["status"] == "pass"
            assert result["check_results"]["vitest"]["status"] == "fail"

    def test_record_check_result_overwrites_previous(self, initialized_db):
        """A second result for the same check overwrites the first."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result

            create_task(task_id="cr4", file_path="/cr4.md", checks=["pytest-submodule"])

            record_check_result("cr4", "pytest-submodule", "fail", "first run failed")
            result = record_check_result("cr4", "pytest-submodule", "pass", "retry passed")

            assert result["check_results"]["pytest-submodule"]["status"] == "pass"
            assert result["check_results"]["pytest-submodule"]["summary"] == "retry passed"

    def test_record_check_result_records_history(self, initialized_db):
        """record_check_result adds a history event."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, get_task_history

            create_task(task_id="cr5", file_path="/cr5.md", checks=["pytest-submodule"])
            record_check_result("cr5", "pytest-submodule", "pass", "All passed")

            history = get_task_history("cr5")
            check_events = [h for h in history if h["event"] == "check_pass"]
            assert len(check_events) == 1
            assert "pytest-submodule" in check_events[0]["details"]

    def test_check_results_round_trip_via_get_task(self, initialized_db):
        """check_results stored in DB round-trips correctly through get_task."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, get_task

            create_task(task_id="cr6", file_path="/cr6.md", checks=["pytest-submodule"])
            record_check_result("cr6", "pytest-submodule", "pass", "OK")

            task = get_task("cr6")
            assert isinstance(task["check_results"], dict)
            assert task["check_results"]["pytest-submodule"]["status"] == "pass"

    def test_check_results_round_trip_via_list_tasks(self, initialized_db):
        """check_results round-trips correctly through list_tasks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, list_tasks

            create_task(task_id="cr7", file_path="/cr7.md", checks=["pytest-submodule"])
            record_check_result("cr7", "pytest-submodule", "fail", "broken")

            tasks = list_tasks(queue="incoming")
            by_id = {t["id"]: t for t in tasks}
            assert by_id["cr7"]["check_results"]["pytest-submodule"]["status"] == "fail"

    def test_empty_check_results_returns_empty_dict(self, initialized_db):
        """Task with no check results returns empty dict."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            create_task(task_id="cr8", file_path="/cr8.md", checks=["pytest-submodule"])

            task = get_task("cr8")
            assert task["check_results"] == {}


class TestAllChecksPassed:
    """Tests for all_checks_passed()."""

    def test_all_passed(self, initialized_db):
        """Returns True when all checks pass."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, all_checks_passed

            create_task(task_id="acp1", file_path="/acp1.md", checks=["pytest-submodule"])
            record_check_result("acp1", "pytest-submodule", "pass")

            passed, not_passed = all_checks_passed("acp1")
            assert passed is True
            assert not_passed == []

    def test_one_failed(self, initialized_db):
        """Returns False when a check fails."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, all_checks_passed

            create_task(task_id="acp2", file_path="/acp2.md", checks=["pytest-submodule", "vitest"])
            record_check_result("acp2", "pytest-submodule", "pass")
            record_check_result("acp2", "vitest", "fail")

            passed, not_passed = all_checks_passed("acp2")
            assert passed is False
            assert "vitest" in not_passed

    def test_none_run(self, initialized_db):
        """Returns False when no checks have been run yet."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, all_checks_passed

            create_task(task_id="acp3", file_path="/acp3.md", checks=["pytest-submodule"])

            passed, not_passed = all_checks_passed("acp3")
            assert passed is False
            assert "pytest-submodule" in not_passed

    def test_no_checks_defined(self, initialized_db):
        """Returns True when task has no checks defined."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, all_checks_passed

            create_task(task_id="acp4", file_path="/acp4.md")

            passed, not_passed = all_checks_passed("acp4")
            assert passed is True
            assert not_passed == []

    def test_nonexistent_task(self, initialized_db):
        """Returns False for missing task."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import all_checks_passed

            passed, not_passed = all_checks_passed("nonexistent")
            assert passed is False


class TestGetCheckFeedback:
    """Tests for get_check_feedback()."""

    def test_feedback_from_failed_check(self, initialized_db):
        """Returns formatted feedback for failed checks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, get_check_feedback

            create_task(task_id="gcf1", file_path="/gcf1.md", checks=["pytest-submodule"])
            record_check_result("gcf1", "pytest-submodule", "fail", "3 tests failed")

            feedback = get_check_feedback("gcf1")
            assert "pytest-submodule" in feedback
            assert "FAILED" in feedback
            assert "3 tests failed" in feedback

    def test_no_feedback_when_all_pass(self, initialized_db):
        """Returns empty string when all checks pass."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, get_check_feedback

            create_task(task_id="gcf2", file_path="/gcf2.md", checks=["pytest-submodule"])
            record_check_result("gcf2", "pytest-submodule", "pass", "OK")

            feedback = get_check_feedback("gcf2")
            assert feedback == ""

    def test_nonexistent_task(self, initialized_db):
        """Returns empty string for missing task."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import get_check_feedback

            feedback = get_check_feedback("nonexistent")
            assert feedback == ""


# ---------------------------------------------------------------------------
# Reports: checking vs in_review split
# ---------------------------------------------------------------------------


class TestReportsCheckingSplit:
    """Tests that reports.py correctly splits provisional tasks by check state."""

    def test_task_with_pending_checks_goes_to_checking(self, mock_config, initialized_db):
        """A provisional task with pending checks appears in 'checking'."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.reports import _gather_work

            prov_dir = mock_config / "shared" / "queue" / "provisional"
            prov_dir.mkdir(parents=True, exist_ok=True)

            fp = prov_dir / "TASK-rck1.md"
            fp.write_text("# [TASK-rck1] Task with check\nROLE: orchestrator_impl\n")
            create_task(task_id="rck1", file_path=str(fp), role="orchestrator_impl", checks=["pytest-submodule"])
            update_task_queue("rck1", "provisional", commits_count=2)

            work = _gather_work()

            checking_ids = [t["id"] for t in work["checking"]]
            review_ids = [t["id"] for t in work["in_review"]]
            assert "rck1" in checking_ids
            assert "rck1" not in review_ids

    def test_task_with_passed_checks_goes_to_in_review(self, mock_config, initialized_db):
        """A provisional task where all checks passed appears in 'in_review'."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, record_check_result
            from orchestrator.reports import _gather_work

            prov_dir = mock_config / "shared" / "queue" / "provisional"
            prov_dir.mkdir(parents=True, exist_ok=True)

            fp = prov_dir / "TASK-rck2.md"
            fp.write_text("# [TASK-rck2] Task with passed check\nROLE: orchestrator_impl\n")
            create_task(task_id="rck2", file_path=str(fp), role="orchestrator_impl", checks=["pytest-submodule"])
            update_task_queue("rck2", "provisional", commits_count=3)
            record_check_result("rck2", "pytest-submodule", "pass", "All tests passed")

            work = _gather_work()

            checking_ids = [t["id"] for t in work["checking"]]
            review_ids = [t["id"] for t in work["in_review"]]
            assert "rck2" not in checking_ids
            assert "rck2" in review_ids

    def test_task_with_no_checks_goes_to_in_review(self, mock_config, initialized_db):
        """A provisional task with no checks defined appears in 'in_review'."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.reports import _gather_work

            prov_dir = mock_config / "shared" / "queue" / "provisional"
            prov_dir.mkdir(parents=True, exist_ok=True)

            fp = prov_dir / "TASK-rck3.md"
            fp.write_text("# [TASK-rck3] Regular task\nROLE: implement\n")
            create_task(task_id="rck3", file_path=str(fp), role="implement")
            update_task_queue("rck3", "provisional", commits_count=1)

            work = _gather_work()

            checking_ids = [t["id"] for t in work["checking"]]
            review_ids = [t["id"] for t in work["in_review"]]
            assert "rck3" not in checking_ids
            assert "rck3" in review_ids

    def test_task_with_failed_check_in_checking(self, mock_config, initialized_db):
        """A provisional task with a failed check appears in 'checking' (not yet rejected)."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, record_check_result
            from orchestrator.reports import _gather_work

            prov_dir = mock_config / "shared" / "queue" / "provisional"
            prov_dir.mkdir(parents=True, exist_ok=True)

            fp = prov_dir / "TASK-rck4.md"
            fp.write_text("# [TASK-rck4] Failed check task\nROLE: orchestrator_impl\n")
            create_task(task_id="rck4", file_path=str(fp), role="orchestrator_impl", checks=["pytest-submodule"])
            update_task_queue("rck4", "provisional", commits_count=2)
            record_check_result("rck4", "pytest-submodule", "fail", "Tests broken")

            work = _gather_work()

            checking_ids = [t["id"] for t in work["checking"]]
            review_ids = [t["id"] for t in work["in_review"]]
            assert "rck4" in checking_ids
            assert "rck4" not in review_ids

    def test_format_task_includes_check_results(self):
        """_format_task includes checks and check_results fields."""
        from orchestrator.reports import _format_task

        task = {
            "id": "ft1",
            "title": "Test task",
            "checks": ["pytest-submodule"],
            "check_results": {"pytest-submodule": {"status": "pass", "summary": "OK"}},
        }

        result = _format_task(task)
        assert result["checks"] == ["pytest-submodule"]
        assert result["check_results"]["pytest-submodule"]["status"] == "pass"


# ---------------------------------------------------------------------------
# Validator: skip tasks with pending checks
# ---------------------------------------------------------------------------


class TestValidatorSkipsPendingChecks:
    """Tests that the validator skips tasks with pending automated checks."""

    def test_validator_skips_task_with_pending_checks(self, mock_config, initialized_db):
        """Validator should not reject/accept tasks that have pending automated checks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.roles.validator.is_db_enabled", return_value=True):
                with patch("orchestrator.queue_utils.is_db_enabled", return_value=True):
                    with patch("orchestrator.queue_utils.get_queue_dir", return_value=mock_config / "shared" / "queue"):
                        with patch("orchestrator.roles.validator.get_validation_config", return_value={
                            "require_commits": True,
                            "max_attempts_before_planning": 3,
                            "claim_timeout_minutes": 60,
                        }):
                            with patch.dict(os.environ, {
                                "AGENT_NAME": "test-validator",
                                "AGENT_ID": "0",
                                "AGENT_ROLE": "validator",
                                "PARENT_PROJECT": str(mock_config.parent),
                                "WORKTREE": str(mock_config.parent),
                                "SHARED_DIR": str(mock_config / "shared"),
                                "ORCHESTRATOR_DIR": str(mock_config),
                            }):
                                from orchestrator.roles.validator import ValidatorRole
                                from orchestrator.db import (
                                    create_task, claim_task, submit_completion, get_task,
                                )

                                # Create task with checks, submit with commits
                                create_task(
                                    task_id="vskip1",
                                    file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-vskip1.md"),
                                    role="orchestrator_impl",
                                    checks=["pytest-submodule"],
                                )
                                claim_task()
                                submit_completion("vskip1", commits_count=3, turns_used=20)

                                # Create task file
                                prov_dir = mock_config / "shared" / "queue" / "provisional"
                                prov_dir.mkdir(parents=True, exist_ok=True)
                                (prov_dir / "TASK-vskip1.md").write_text(
                                    "# [TASK-vskip1] Orch task\nROLE: orchestrator_impl\n"
                                )

                                # Run validator
                                role = ValidatorRole()
                                result = role.run()
                                assert result == 0

                                # Task should STILL be in provisional (not moved to done or incoming)
                                task = get_task("vskip1")
                                assert task["queue"] == "provisional", (
                                    f"Validator should skip task with pending checks, "
                                    f"but task was moved to {task['queue']}"
                                )

    def test_validator_processes_task_after_checks_pass(self, mock_config, initialized_db):
        """After checks pass, validator should accept the task normally."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.roles.validator.is_db_enabled", return_value=True):
                with patch("orchestrator.queue_utils.is_db_enabled", return_value=True):
                    with patch("orchestrator.queue_utils.get_queue_dir", return_value=mock_config / "shared" / "queue"):
                        with patch("orchestrator.roles.validator.get_validation_config", return_value={
                            "require_commits": True,
                            "max_attempts_before_planning": 3,
                            "claim_timeout_minutes": 60,
                        }):
                            with patch.dict(os.environ, {
                                "AGENT_NAME": "test-validator",
                                "AGENT_ID": "0",
                                "AGENT_ROLE": "validator",
                                "PARENT_PROJECT": str(mock_config.parent),
                                "WORKTREE": str(mock_config.parent),
                                "SHARED_DIR": str(mock_config / "shared"),
                                "ORCHESTRATOR_DIR": str(mock_config),
                            }):
                                from orchestrator.roles.validator import ValidatorRole
                                from orchestrator.db import (
                                    create_task, claim_task, submit_completion,
                                    record_check_result, get_task,
                                )

                                # Create task, submit, and mark check as passed
                                create_task(
                                    task_id="vskip2",
                                    file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-vskip2.md"),
                                    role="orchestrator_impl",
                                    checks=["pytest-submodule"],
                                )
                                claim_task()
                                submit_completion("vskip2", commits_count=3, turns_used=20)
                                record_check_result("vskip2", "pytest-submodule", "pass", "OK")

                                # Create task file
                                prov_dir = mock_config / "shared" / "queue" / "provisional"
                                prov_dir.mkdir(parents=True, exist_ok=True)
                                (prov_dir / "TASK-vskip2.md").write_text(
                                    "# [TASK-vskip2] Orch task\nROLE: orchestrator_impl\n"
                                )

                                # Run validator
                                role = ValidatorRole()
                                result = role.run()
                                assert result == 0

                                # Task should be accepted (all checks passed, has commits)
                                task = get_task("vskip2")
                                assert task["queue"] == "done"


# ---------------------------------------------------------------------------
# Backpressure: check_runner
# ---------------------------------------------------------------------------


class TestCheckRunnerBackpressure:
    """Tests for check_runner backpressure function."""

    def test_no_provisional_tasks_blocks(self, mock_config, initialized_db):
        """Check runner should be blocked when no provisional tasks exist."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.backpressure.is_db_enabled", return_value=True):
                from orchestrator.backpressure import check_check_runner_backpressure

                can_proceed, reason = check_check_runner_backpressure()
                assert can_proceed is False
                assert "no_pending_checks" in reason

    def test_provisional_task_with_pending_check_allows(self, mock_config, initialized_db):
        """Check runner should proceed when there's a task with pending checks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.backpressure.is_db_enabled", return_value=True):
                from orchestrator.db import create_task, update_task_queue
                from orchestrator.backpressure import check_check_runner_backpressure

                create_task(task_id="bp1", file_path="/bp1.md", checks=["pytest-submodule"])
                update_task_queue("bp1", "provisional", commits_count=2)

                can_proceed, reason = check_check_runner_backpressure()
                assert can_proceed is True

    def test_provisional_task_with_all_checks_done_blocks(self, mock_config, initialized_db):
        """Check runner should be blocked when all checks are already done."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.backpressure.is_db_enabled", return_value=True):
                from orchestrator.db import create_task, update_task_queue, record_check_result
                from orchestrator.backpressure import check_check_runner_backpressure

                create_task(task_id="bp2", file_path="/bp2.md", checks=["pytest-submodule"])
                update_task_queue("bp2", "provisional", commits_count=2)
                record_check_result("bp2", "pytest-submodule", "pass", "OK")

                can_proceed, reason = check_check_runner_backpressure()
                assert can_proceed is False

    def test_provisional_task_without_checks_blocks(self, mock_config, initialized_db):
        """Check runner should be blocked when provisional tasks have no checks defined."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.backpressure.is_db_enabled", return_value=True):
                from orchestrator.db import create_task, update_task_queue
                from orchestrator.backpressure import check_check_runner_backpressure

                create_task(task_id="bp3", file_path="/bp3.md")  # No checks
                update_task_queue("bp3", "provisional", commits_count=1)

                can_proceed, reason = check_check_runner_backpressure()
                assert can_proceed is False

    def test_db_disabled_blocks(self):
        """Check runner should be blocked when DB is not enabled."""
        with patch("orchestrator.backpressure.is_db_enabled", return_value=False):
            from orchestrator.backpressure import check_check_runner_backpressure

            can_proceed, reason = check_check_runner_backpressure()
            assert can_proceed is False
            assert "requires_db" in reason

    def test_role_checks_map_includes_check_runner(self):
        """ROLE_CHECKS map should include check_runner."""
        from orchestrator.backpressure import ROLE_CHECKS

        assert "check_runner" in ROLE_CHECKS


# ---------------------------------------------------------------------------
# CheckRunnerRole unit tests
# ---------------------------------------------------------------------------


class TestCheckRunnerRole:
    """Tests for the CheckRunnerRole class."""

    def test_check_runner_requires_db(self, mock_config):
        """Check runner returns early if DB not enabled."""
        with patch("orchestrator.roles.check_runner.is_db_enabled", return_value=False):
            with patch.dict(os.environ, {
                "AGENT_NAME": "test-check-runner",
                "AGENT_ID": "0",
                "AGENT_ROLE": "check_runner",
                "PARENT_PROJECT": str(mock_config.parent),
                "WORKTREE": str(mock_config.parent),
                "SHARED_DIR": str(mock_config / "shared"),
                "ORCHESTRATOR_DIR": str(mock_config),
            }):
                from orchestrator.roles.check_runner import CheckRunnerRole

                role = CheckRunnerRole()
                result = role.run()
                assert result == 0

    def test_check_runner_skips_tasks_without_checks(self, mock_config, initialized_db):
        """Check runner should skip provisional tasks that have no checks defined."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.roles.check_runner.is_db_enabled", return_value=True):
                with patch("orchestrator.queue_utils.is_db_enabled", return_value=True):
                    with patch("orchestrator.queue_utils.get_queue_dir", return_value=mock_config / "shared" / "queue"):
                        with patch.dict(os.environ, {
                            "AGENT_NAME": "test-check-runner",
                            "AGENT_ID": "0",
                            "AGENT_ROLE": "check_runner",
                            "PARENT_PROJECT": str(mock_config.parent),
                            "WORKTREE": str(mock_config.parent),
                            "SHARED_DIR": str(mock_config / "shared"),
                            "ORCHESTRATOR_DIR": str(mock_config),
                        }):
                            from orchestrator.roles.check_runner import CheckRunnerRole
                            from orchestrator.db import create_task, update_task_queue, get_task

                            # Create provisional task with NO checks
                            create_task(task_id="ckr1", file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-ckr1.md"))
                            update_task_queue("ckr1", "provisional", commits_count=1)

                            prov_dir = mock_config / "shared" / "queue" / "provisional"
                            prov_dir.mkdir(parents=True, exist_ok=True)
                            (prov_dir / "TASK-ckr1.md").write_text("# [TASK-ckr1] No check task\n")

                            role = CheckRunnerRole()
                            result = role.run()
                            assert result == 0

                            # Task should still be in provisional, untouched
                            task = get_task("ckr1")
                            assert task["queue"] == "provisional"
                            assert task["check_results"] == {}

    def test_check_runner_skips_already_checked_tasks(self, mock_config, initialized_db):
        """Check runner should skip tasks where all checks have been run."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.roles.check_runner.is_db_enabled", return_value=True):
                with patch("orchestrator.queue_utils.is_db_enabled", return_value=True):
                    with patch("orchestrator.queue_utils.get_queue_dir", return_value=mock_config / "shared" / "queue"):
                        with patch.dict(os.environ, {
                            "AGENT_NAME": "test-check-runner",
                            "AGENT_ID": "0",
                            "AGENT_ROLE": "check_runner",
                            "PARENT_PROJECT": str(mock_config.parent),
                            "WORKTREE": str(mock_config.parent),
                            "SHARED_DIR": str(mock_config / "shared"),
                            "ORCHESTRATOR_DIR": str(mock_config),
                        }):
                            from orchestrator.roles.check_runner import CheckRunnerRole
                            from orchestrator.db import create_task, update_task_queue, record_check_result, get_task

                            # Create provisional task with check already passed
                            create_task(
                                task_id="ckr2",
                                file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-ckr2.md"),
                                checks=["pytest-submodule"],
                            )
                            update_task_queue("ckr2", "provisional", commits_count=2)
                            record_check_result("ckr2", "pytest-submodule", "pass", "OK")

                            prov_dir = mock_config / "shared" / "queue" / "provisional"
                            prov_dir.mkdir(parents=True, exist_ok=True)
                            (prov_dir / "TASK-ckr2.md").write_text("# [TASK-ckr2] Checked task\n")

                            role = CheckRunnerRole()
                            result = role.run()
                            assert result == 0

                            # Task should still be in provisional (not rejected, not re-checked)
                            task = get_task("ckr2")
                            assert task["queue"] == "provisional"


# ---------------------------------------------------------------------------
# Schema migration test
# ---------------------------------------------------------------------------


class TestSchemaMigrationV6:
    """Tests for the v5→v6 migration adding check_results column."""

    def test_migrate_from_v5_adds_check_results(self, mock_config, db_path):
        """Migrating from v5 to v6 adds the check_results column."""
        with patch("orchestrator.db.get_database_path", return_value=db_path):
            from orchestrator.db import init_schema, get_connection, SCHEMA_VERSION

            # Initialize the schema (creates v6 directly)
            init_schema()

            # Verify check_results column exists
            with get_connection() as conn:
                cursor = conn.execute("PRAGMA table_info(tasks)")
                columns = {row["name"]: row for row in cursor.fetchall()}
                assert "check_results" in columns

            # Verify schema version
            assert SCHEMA_VERSION == 6


# ---------------------------------------------------------------------------
# VALID_CHECK_TYPES constant
# ---------------------------------------------------------------------------


class TestValidCheckTypes:
    """Tests for the VALID_CHECK_TYPES constant."""

    def test_valid_check_types_includes_pytest_submodule(self):
        """VALID_CHECK_TYPES should include pytest-submodule."""
        from orchestrator.roles.check_runner import VALID_CHECK_TYPES

        assert "pytest-submodule" in VALID_CHECK_TYPES

    def test_valid_check_types_includes_gk_testing(self):
        """VALID_CHECK_TYPES should include gk-testing-octopoid."""
        from orchestrator.roles.check_runner import VALID_CHECK_TYPES

        assert "gk-testing-octopoid" in VALID_CHECK_TYPES


# ---------------------------------------------------------------------------
# gk-testing-octopoid check
# ---------------------------------------------------------------------------


class TestGkTestingCheck:
    """Tests for the gk-testing-octopoid check type."""

    def test_gk_testing_dispatched_from_run(self, mock_config, initialized_db):
        """CheckRunnerRole.run() dispatches to _run_gk_testing for gk-testing-octopoid checks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.roles.check_runner.is_db_enabled", return_value=True):
                with patch("orchestrator.queue_utils.is_db_enabled", return_value=True):
                    with patch("orchestrator.queue_utils.get_queue_dir", return_value=mock_config / "shared" / "queue"):
                        with patch.dict(os.environ, {
                            "AGENT_NAME": "test-check-runner",
                            "AGENT_ID": "0",
                            "AGENT_ROLE": "check_runner",
                            "PARENT_PROJECT": str(mock_config.parent),
                            "WORKTREE": str(mock_config.parent),
                            "SHARED_DIR": str(mock_config / "shared"),
                            "ORCHESTRATOR_DIR": str(mock_config),
                        }):
                            from orchestrator.roles.check_runner import CheckRunnerRole
                            from orchestrator.db import create_task, update_task_queue

                            # Create provisional task with gk-testing-octopoid check
                            prov_dir = mock_config / "shared" / "queue" / "provisional"
                            prov_dir.mkdir(parents=True, exist_ok=True)
                            fp = prov_dir / "TASK-gkt1.md"
                            fp.write_text("# [TASK-gkt1] GK test task\nROLE: orchestrator_impl\n")

                            create_task(
                                task_id="gkt1",
                                file_path=str(fp),
                                role="orchestrator_impl",
                                checks=["gk-testing-octopoid"],
                            )
                            update_task_queue("gkt1", "provisional", commits_count=2)

                            role = CheckRunnerRole()
                            # Mock _run_gk_testing to verify it gets called
                            role._run_gk_testing = MagicMock()

                            role.run()

                            role._run_gk_testing.assert_called_once()
                            call_args = role._run_gk_testing.call_args[0]
                            assert call_args[0] == "gkt1"
                            assert call_args[1]["id"] == "gkt1"

    def test_gk_testing_fails_no_worktree(self, mock_config, initialized_db):
        """gk-testing-octopoid records fail when agent worktree not found."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.roles.check_runner.is_db_enabled", return_value=True):
                with patch("orchestrator.queue_utils.is_db_enabled", return_value=True):
                    with patch("orchestrator.queue_utils.get_queue_dir", return_value=mock_config / "shared" / "queue"):
                        with patch.dict(os.environ, {
                            "AGENT_NAME": "test-check-runner",
                            "AGENT_ID": "0",
                            "AGENT_ROLE": "check_runner",
                            "PARENT_PROJECT": str(mock_config.parent),
                            "WORKTREE": str(mock_config.parent),
                            "SHARED_DIR": str(mock_config / "shared"),
                            "ORCHESTRATOR_DIR": str(mock_config),
                        }):
                            from orchestrator.roles.check_runner import CheckRunnerRole
                            from orchestrator.db import create_task, update_task_queue, get_task

                            # Create provisional task with gk-testing-octopoid check, no claimed_by
                            prov_dir = mock_config / "shared" / "queue" / "provisional"
                            prov_dir.mkdir(parents=True, exist_ok=True)
                            fp = prov_dir / "TASK-gkt2.md"
                            fp.write_text("# [TASK-gkt2] GK test task\nROLE: orchestrator_impl\n")

                            create_task(
                                task_id="gkt2",
                                file_path=str(fp),
                                role="orchestrator_impl",
                                checks=["gk-testing-octopoid"],
                            )
                            update_task_queue("gkt2", "provisional", commits_count=2)

                            role = CheckRunnerRole()
                            # Mock _find_agent_worktree to return None (no worktree found)
                            role._find_agent_worktree = MagicMock(return_value=None)
                            role._run_gk_testing("gkt2", {"id": "gkt2", "claimed_by": None})

                            # Check that the result was recorded as fail
                            task = get_task("gkt2")
                            assert task["check_results"]["gk-testing-octopoid"]["status"] == "fail"
                            assert "worktree" in task["check_results"]["gk-testing-octopoid"]["summary"].lower()

    def test_gk_testing_fails_no_submodule(self, mock_config, initialized_db):
        """gk-testing-octopoid records fail when orchestrator submodule not found."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch.dict(os.environ, {
                "AGENT_NAME": "test-check-runner",
                "AGENT_ID": "0",
                "AGENT_ROLE": "check_runner",
                "PARENT_PROJECT": str(mock_config.parent),
                "WORKTREE": str(mock_config.parent),
                "SHARED_DIR": str(mock_config / "shared"),
                "ORCHESTRATOR_DIR": str(mock_config),
            }):
                from orchestrator.roles.check_runner import CheckRunnerRole
                from orchestrator.db import create_task, get_task

                create_task(
                    task_id="gkt3",
                    file_path="/gkt3.md",
                    checks=["gk-testing-octopoid"],
                )

                # Create a fake agent worktree WITHOUT orchestrator submodule
                agent_dir = mock_config / "agents" / "fake-agent" / "worktree"
                agent_dir.mkdir(parents=True, exist_ok=True)

                role = CheckRunnerRole()
                # Mock _find_agent_worktree to return our fake worktree
                role._find_agent_worktree = MagicMock(return_value=agent_dir)

                role._run_gk_testing("gkt3", {"id": "gkt3", "claimed_by": "fake-agent"})

                task = get_task("gkt3")
                assert task["check_results"]["gk-testing-octopoid"]["status"] == "fail"
                assert "submodule" in task["check_results"]["gk-testing-octopoid"]["summary"].lower()

    def test_gk_testing_fails_no_commits(self, mock_config, initialized_db):
        """gk-testing-octopoid records fail when no commits found in submodule."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch.dict(os.environ, {
                "AGENT_NAME": "test-check-runner",
                "AGENT_ID": "0",
                "AGENT_ROLE": "check_runner",
                "PARENT_PROJECT": str(mock_config.parent),
                "WORKTREE": str(mock_config.parent),
                "SHARED_DIR": str(mock_config / "shared"),
                "ORCHESTRATOR_DIR": str(mock_config),
            }):
                from orchestrator.roles.check_runner import CheckRunnerRole
                from orchestrator.db import create_task, get_task

                create_task(
                    task_id="gkt4",
                    file_path="/gkt4.md",
                    checks=["gk-testing-octopoid"],
                )

                # Create a fake agent worktree WITH orchestrator submodule dir
                agent_dir = mock_config / "agents" / "fake-agent" / "worktree"
                orch_dir = agent_dir / "orchestrator"
                orch_dir.mkdir(parents=True, exist_ok=True)

                role = CheckRunnerRole()
                role._find_agent_worktree = MagicMock(return_value=agent_dir)
                role._get_submodule_commits = MagicMock(return_value=[])

                role._run_gk_testing("gkt4", {"id": "gkt4", "claimed_by": "fake-agent"})

                task = get_task("gkt4")
                assert task["check_results"]["gk-testing-octopoid"]["status"] == "fail"
                assert "commit" in task["check_results"]["gk-testing-octopoid"]["summary"].lower()

    def test_gk_testing_fails_rebase_conflict(self, mock_config, initialized_db):
        """gk-testing-octopoid records fail with conflict details on rebase failure."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch.dict(os.environ, {
                "AGENT_NAME": "test-check-runner",
                "AGENT_ID": "0",
                "AGENT_ROLE": "check_runner",
                "PARENT_PROJECT": str(mock_config.parent),
                "WORKTREE": str(mock_config.parent),
                "SHARED_DIR": str(mock_config / "shared"),
                "ORCHESTRATOR_DIR": str(mock_config),
            }):
                from orchestrator.roles.check_runner import CheckRunnerRole
                from orchestrator.db import create_task, get_task

                create_task(
                    task_id="gkt5",
                    file_path="/gkt5.md",
                    checks=["gk-testing-octopoid"],
                )

                # Create fake dirs
                agent_dir = mock_config / "agents" / "fake-agent" / "worktree"
                orch_dir = agent_dir / "orchestrator"
                orch_dir.mkdir(parents=True, exist_ok=True)
                review_dir = mock_config / "agents" / "review-worktree" / "orchestrator"
                review_dir.mkdir(parents=True, exist_ok=True)

                role = CheckRunnerRole()
                role._find_agent_worktree = MagicMock(return_value=agent_dir)
                role._get_submodule_commits = MagicMock(return_value=["abc123", "def456"])
                role._setup_clean_submodule = MagicMock(return_value=(True, ""))
                role._check_divergence = MagicMock(return_value="2 commit(s) landed on sqlite-model")
                role._rebase_commits = MagicMock(
                    return_value=(False, "Conflict applying commit `abc123` (feat: add thing)")
                )

                role._run_gk_testing("gkt5", {"id": "gkt5", "claimed_by": "fake-agent"})

                task = get_task("gkt5")
                result = task["check_results"]["gk-testing-octopoid"]
                assert result["status"] == "fail"
                assert "Rebase Failed" in result["summary"]
                assert "Conflict" in result["summary"]
                assert "sqlite-model" in result["summary"]

    def test_gk_testing_fails_test_failure(self, mock_config, initialized_db):
        """gk-testing-octopoid records fail with test output on pytest failure."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch.dict(os.environ, {
                "AGENT_NAME": "test-check-runner",
                "AGENT_ID": "0",
                "AGENT_ROLE": "check_runner",
                "PARENT_PROJECT": str(mock_config.parent),
                "WORKTREE": str(mock_config.parent),
                "SHARED_DIR": str(mock_config / "shared"),
                "ORCHESTRATOR_DIR": str(mock_config),
            }):
                from orchestrator.roles.check_runner import CheckRunnerRole
                from orchestrator.db import create_task, get_task

                create_task(
                    task_id="gkt6",
                    file_path="/gkt6.md",
                    checks=["gk-testing-octopoid"],
                )

                # Create fake dirs
                agent_dir = mock_config / "agents" / "fake-agent" / "worktree"
                orch_dir = agent_dir / "orchestrator"
                orch_dir.mkdir(parents=True, exist_ok=True)
                review_dir = mock_config / "agents" / "review-worktree" / "orchestrator"
                review_dir.mkdir(parents=True, exist_ok=True)

                role = CheckRunnerRole()
                role._find_agent_worktree = MagicMock(return_value=agent_dir)
                role._get_submodule_commits = MagicMock(return_value=["abc123"])
                role._setup_clean_submodule = MagicMock(return_value=(True, ""))
                role._check_divergence = MagicMock(return_value="")
                role._rebase_commits = MagicMock(return_value=(True, ""))
                role._run_pytest = MagicMock(
                    return_value=(False, "FAILED test_something.py::test_x - AssertionError")
                )

                role._run_gk_testing("gkt6", {"id": "gkt6", "claimed_by": "fake-agent"})

                task = get_task("gkt6")
                result = task["check_results"]["gk-testing-octopoid"]
                assert result["status"] == "fail"
                assert "Test Failures" in result["summary"]
                assert "FAILED" in result["summary"]

    def test_gk_testing_passes(self, mock_config, initialized_db):
        """gk-testing-octopoid records pass when rebase and tests succeed."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch.dict(os.environ, {
                "AGENT_NAME": "test-check-runner",
                "AGENT_ID": "0",
                "AGENT_ROLE": "check_runner",
                "PARENT_PROJECT": str(mock_config.parent),
                "WORKTREE": str(mock_config.parent),
                "SHARED_DIR": str(mock_config / "shared"),
                "ORCHESTRATOR_DIR": str(mock_config),
            }):
                from orchestrator.roles.check_runner import CheckRunnerRole
                from orchestrator.db import create_task, get_task

                create_task(
                    task_id="gkt7",
                    file_path="/gkt7.md",
                    checks=["gk-testing-octopoid"],
                )

                # Create fake dirs
                agent_dir = mock_config / "agents" / "fake-agent" / "worktree"
                orch_dir = agent_dir / "orchestrator"
                orch_dir.mkdir(parents=True, exist_ok=True)
                review_dir = mock_config / "agents" / "review-worktree" / "orchestrator"
                review_dir.mkdir(parents=True, exist_ok=True)

                role = CheckRunnerRole()
                role._find_agent_worktree = MagicMock(return_value=agent_dir)
                role._get_submodule_commits = MagicMock(return_value=["abc123", "def456"])
                role._setup_clean_submodule = MagicMock(return_value=(True, ""))
                role._check_divergence = MagicMock(return_value="")
                role._rebase_commits = MagicMock(return_value=(True, ""))
                role._run_pytest = MagicMock(return_value=(True, "3 passed"))

                role._run_gk_testing("gkt7", {"id": "gkt7", "claimed_by": "fake-agent"})

                task = get_task("gkt7")
                result = task["check_results"]["gk-testing-octopoid"]
                assert result["status"] == "pass"
                assert "2 commit(s)" in result["summary"]
                assert "passed" in result["summary"].lower()

    def test_gk_testing_pass_with_divergence_note(self, mock_config, initialized_db):
        """gk-testing-octopoid pass summary notes when base had diverged."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch.dict(os.environ, {
                "AGENT_NAME": "test-check-runner",
                "AGENT_ID": "0",
                "AGENT_ROLE": "check_runner",
                "PARENT_PROJECT": str(mock_config.parent),
                "WORKTREE": str(mock_config.parent),
                "SHARED_DIR": str(mock_config / "shared"),
                "ORCHESTRATOR_DIR": str(mock_config),
            }):
                from orchestrator.roles.check_runner import CheckRunnerRole
                from orchestrator.db import create_task, get_task

                create_task(
                    task_id="gkt8",
                    file_path="/gkt8.md",
                    checks=["gk-testing-octopoid"],
                )

                agent_dir = mock_config / "agents" / "fake-agent" / "worktree"
                orch_dir = agent_dir / "orchestrator"
                orch_dir.mkdir(parents=True, exist_ok=True)
                review_dir = mock_config / "agents" / "review-worktree" / "orchestrator"
                review_dir.mkdir(parents=True, exist_ok=True)

                role = CheckRunnerRole()
                role._find_agent_worktree = MagicMock(return_value=agent_dir)
                role._get_submodule_commits = MagicMock(return_value=["abc123"])
                role._setup_clean_submodule = MagicMock(return_value=(True, ""))
                role._check_divergence = MagicMock(return_value="3 commit(s) landed on sqlite-model")
                role._rebase_commits = MagicMock(return_value=(True, ""))
                role._run_pytest = MagicMock(return_value=(True, "all passed"))

                role._run_gk_testing("gkt8", {"id": "gkt8", "claimed_by": "fake-agent"})

                task = get_task("gkt8")
                result = task["check_results"]["gk-testing-octopoid"]
                assert result["status"] == "pass"
                assert "Rebase succeeded" in result["summary"]

    def test_gk_testing_fails_setup(self, mock_config, initialized_db):
        """gk-testing-octopoid records fail when environment setup fails."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch.dict(os.environ, {
                "AGENT_NAME": "test-check-runner",
                "AGENT_ID": "0",
                "AGENT_ROLE": "check_runner",
                "PARENT_PROJECT": str(mock_config.parent),
                "WORKTREE": str(mock_config.parent),
                "SHARED_DIR": str(mock_config / "shared"),
                "ORCHESTRATOR_DIR": str(mock_config),
            }):
                from orchestrator.roles.check_runner import CheckRunnerRole
                from orchestrator.db import create_task, get_task

                create_task(
                    task_id="gkt9",
                    file_path="/gkt9.md",
                    checks=["gk-testing-octopoid"],
                )

                agent_dir = mock_config / "agents" / "fake-agent" / "worktree"
                orch_dir = agent_dir / "orchestrator"
                orch_dir.mkdir(parents=True, exist_ok=True)
                review_dir = mock_config / "agents" / "review-worktree" / "orchestrator"
                review_dir.mkdir(parents=True, exist_ok=True)

                role = CheckRunnerRole()
                role._find_agent_worktree = MagicMock(return_value=agent_dir)
                role._get_submodule_commits = MagicMock(return_value=["abc123"])
                role._setup_clean_submodule = MagicMock(
                    return_value=(False, "git fetch failed: permission denied")
                )

                role._run_gk_testing("gkt9", {"id": "gkt9", "claimed_by": "fake-agent"})

                task = get_task("gkt9")
                result = task["check_results"]["gk-testing-octopoid"]
                assert result["status"] == "fail"
                assert "set up" in result["summary"].lower()

    def test_gk_testing_test_failure_with_divergence_context(self, mock_config, initialized_db):
        """gk-testing-octopoid test failure includes divergence context when base moved."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch.dict(os.environ, {
                "AGENT_NAME": "test-check-runner",
                "AGENT_ID": "0",
                "AGENT_ROLE": "check_runner",
                "PARENT_PROJECT": str(mock_config.parent),
                "WORKTREE": str(mock_config.parent),
                "SHARED_DIR": str(mock_config / "shared"),
                "ORCHESTRATOR_DIR": str(mock_config),
            }):
                from orchestrator.roles.check_runner import CheckRunnerRole
                from orchestrator.db import create_task, get_task

                create_task(
                    task_id="gkt10",
                    file_path="/gkt10.md",
                    checks=["gk-testing-octopoid"],
                )

                agent_dir = mock_config / "agents" / "fake-agent" / "worktree"
                orch_dir = agent_dir / "orchestrator"
                orch_dir.mkdir(parents=True, exist_ok=True)
                review_dir = mock_config / "agents" / "review-worktree" / "orchestrator"
                review_dir.mkdir(parents=True, exist_ok=True)

                role = CheckRunnerRole()
                role._find_agent_worktree = MagicMock(return_value=agent_dir)
                role._get_submodule_commits = MagicMock(return_value=["abc123"])
                role._setup_clean_submodule = MagicMock(return_value=(True, ""))
                role._check_divergence = MagicMock(return_value="5 commit(s) landed on sqlite-model")
                role._rebase_commits = MagicMock(return_value=(True, ""))
                role._run_pytest = MagicMock(return_value=(False, "FAILED test_foo::test_bar"))

                role._run_gk_testing("gkt10", {"id": "gkt10", "claimed_by": "fake-agent"})

                task = get_task("gkt10")
                result = task["check_results"]["gk-testing-octopoid"]
                assert result["status"] == "fail"
                assert "upstream" in result["summary"].lower()
                assert "FAILED" in result["summary"]


# ---------------------------------------------------------------------------
# Default checks for orchestrator_impl
# ---------------------------------------------------------------------------


class TestDefaultChecksForOrchestratorImpl:
    """Tests that orchestrator_impl tasks get gk-testing-octopoid by default."""

    def test_create_task_defaults_to_gk_testing(self, mock_config, initialized_db):
        """create_task with role=orchestrator_impl and no checks defaults to gk-testing-octopoid."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.queue_utils.is_db_enabled", return_value=True):
                with patch("orchestrator.queue_utils.get_queue_dir", return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import create_task
                    from orchestrator.db import get_task

                    task_path = create_task(
                        title="Test orch task",
                        role="orchestrator_impl",
                        context="Test context",
                        acceptance_criteria=["Do the thing"],
                    )

                    # Extract task ID from filename
                    task_id = task_path.stem.replace("TASK-", "")
                    task = get_task(task_id)

                    assert task["checks"] == ["gk-testing-octopoid"]

    def test_create_task_explicit_checks_override_default(self, mock_config, initialized_db):
        """Explicit checks parameter overrides the default for orchestrator_impl."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.queue_utils.is_db_enabled", return_value=True):
                with patch("orchestrator.queue_utils.get_queue_dir", return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import create_task
                    from orchestrator.db import get_task

                    task_path = create_task(
                        title="Test orch task with custom checks",
                        role="orchestrator_impl",
                        context="Test context",
                        acceptance_criteria=["Do the thing"],
                        checks=["pytest-submodule"],
                    )

                    task_id = task_path.stem.replace("TASK-", "")
                    task = get_task(task_id)

                    assert task["checks"] == ["pytest-submodule"]

    def test_backpressure_works_with_gk_testing(self, mock_config, initialized_db):
        """Backpressure check detects pending gk-testing-octopoid checks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.backpressure.is_db_enabled", return_value=True):
                from orchestrator.db import create_task, update_task_queue
                from orchestrator.backpressure import check_check_runner_backpressure

                create_task(task_id="bp_gk1", file_path="/bp_gk1.md", checks=["gk-testing-octopoid"])
                update_task_queue("bp_gk1", "provisional", commits_count=2)

                can_proceed, reason = check_check_runner_backpressure()
                assert can_proceed is True
