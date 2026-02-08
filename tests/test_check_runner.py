"""Tests for check_results DB functions and gatekeeper integration."""

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
                checks=["gk-testing-octopoid"],
            )

            result = record_check_result("cr1", "gk-testing-octopoid", "pass", "All tests passed")

            assert result is not None
            assert result["check_results"]["gk-testing-octopoid"]["status"] == "pass"
            assert result["check_results"]["gk-testing-octopoid"]["summary"] == "All tests passed"
            assert "timestamp" in result["check_results"]["gk-testing-octopoid"]

    def test_record_check_result_fail(self, initialized_db):
        """record_check_result stores a failing result."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, get_task

            create_task(task_id="cr2", file_path="/cr2.md", checks=["gk-testing-octopoid"])

            result = record_check_result("cr2", "gk-testing-octopoid", "fail", "3 tests failed")

            assert result["check_results"]["gk-testing-octopoid"]["status"] == "fail"
            assert "3 tests failed" in result["check_results"]["gk-testing-octopoid"]["summary"]

    def test_record_check_result_nonexistent_task(self, initialized_db):
        """record_check_result returns None for missing task."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import record_check_result

            result = record_check_result("nonexistent", "gk-testing-octopoid", "pass")
            assert result is None

    def test_record_multiple_check_results(self, initialized_db):
        """Multiple checks can be recorded on the same task."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result

            create_task(
                task_id="cr3",
                file_path="/cr3.md",
                checks=["gk-testing-octopoid", "vitest"],
            )

            record_check_result("cr3", "gk-testing-octopoid", "pass", "OK")
            result = record_check_result("cr3", "vitest", "fail", "2 failures")

            assert result["check_results"]["gk-testing-octopoid"]["status"] == "pass"
            assert result["check_results"]["vitest"]["status"] == "fail"

    def test_record_check_result_overwrites_previous(self, initialized_db):
        """A second result for the same check overwrites the first."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result

            create_task(task_id="cr4", file_path="/cr4.md", checks=["gk-testing-octopoid"])

            record_check_result("cr4", "gk-testing-octopoid", "fail", "first run failed")
            result = record_check_result("cr4", "gk-testing-octopoid", "pass", "retry passed")

            assert result["check_results"]["gk-testing-octopoid"]["status"] == "pass"
            assert result["check_results"]["gk-testing-octopoid"]["summary"] == "retry passed"

    def test_record_check_result_records_history(self, initialized_db):
        """record_check_result adds a history event."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, get_task_history

            create_task(task_id="cr5", file_path="/cr5.md", checks=["gk-testing-octopoid"])
            record_check_result("cr5", "gk-testing-octopoid", "pass", "All passed")

            history = get_task_history("cr5")
            check_events = [h for h in history if h["event"] == "check_pass"]
            assert len(check_events) == 1
            assert "gk-testing-octopoid" in check_events[0]["details"]

    def test_check_results_round_trip_via_get_task(self, initialized_db):
        """check_results stored in DB round-trips correctly through get_task."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, get_task

            create_task(task_id="cr6", file_path="/cr6.md", checks=["gk-testing-octopoid"])
            record_check_result("cr6", "gk-testing-octopoid", "pass", "OK")

            task = get_task("cr6")
            assert isinstance(task["check_results"], dict)
            assert task["check_results"]["gk-testing-octopoid"]["status"] == "pass"

    def test_check_results_round_trip_via_list_tasks(self, initialized_db):
        """check_results round-trips correctly through list_tasks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, list_tasks

            create_task(task_id="cr7", file_path="/cr7.md", checks=["gk-testing-octopoid"])
            record_check_result("cr7", "gk-testing-octopoid", "fail", "broken")

            tasks = list_tasks(queue="incoming")
            by_id = {t["id"]: t for t in tasks}
            assert by_id["cr7"]["check_results"]["gk-testing-octopoid"]["status"] == "fail"

    def test_empty_check_results_returns_empty_dict(self, initialized_db):
        """Task with no check results returns empty dict."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, get_task

            create_task(task_id="cr8", file_path="/cr8.md", checks=["gk-testing-octopoid"])

            task = get_task("cr8")
            assert task["check_results"] == {}


class TestAllChecksPassed:
    """Tests for all_checks_passed()."""

    def test_all_passed(self, initialized_db):
        """Returns True when all checks pass."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, all_checks_passed

            create_task(task_id="acp1", file_path="/acp1.md", checks=["gk-testing-octopoid"])
            record_check_result("acp1", "gk-testing-octopoid", "pass")

            passed, not_passed = all_checks_passed("acp1")
            assert passed is True
            assert not_passed == []

    def test_one_failed(self, initialized_db):
        """Returns False when a check fails."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, all_checks_passed

            create_task(task_id="acp2", file_path="/acp2.md", checks=["gk-testing-octopoid", "vitest"])
            record_check_result("acp2", "gk-testing-octopoid", "pass")
            record_check_result("acp2", "vitest", "fail")

            passed, not_passed = all_checks_passed("acp2")
            assert passed is False
            assert "vitest" in not_passed

    def test_none_run(self, initialized_db):
        """Returns False when no checks have been run yet."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, all_checks_passed

            create_task(task_id="acp3", file_path="/acp3.md", checks=["gk-testing-octopoid"])

            passed, not_passed = all_checks_passed("acp3")
            assert passed is False
            assert "gk-testing-octopoid" in not_passed

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

            create_task(task_id="gcf1", file_path="/gcf1.md", checks=["gk-testing-octopoid"])
            record_check_result("gcf1", "gk-testing-octopoid", "fail", "3 tests failed")

            feedback = get_check_feedback("gcf1")
            assert "gk-testing-octopoid" in feedback
            assert "FAILED" in feedback
            assert "3 tests failed" in feedback

    def test_no_feedback_when_all_pass(self, initialized_db):
        """Returns empty string when all checks pass."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            from orchestrator.db import create_task, record_check_result, get_check_feedback

            create_task(task_id="gcf2", file_path="/gcf2.md", checks=["gk-testing-octopoid"])
            record_check_result("gcf2", "gk-testing-octopoid", "pass", "OK")

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
            create_task(task_id="rck1", file_path=str(fp), role="orchestrator_impl", checks=["gk-testing-octopoid"])
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
            create_task(task_id="rck2", file_path=str(fp), role="orchestrator_impl", checks=["gk-testing-octopoid"])
            update_task_queue("rck2", "provisional", commits_count=3)
            record_check_result("rck2", "gk-testing-octopoid", "pass", "All tests passed")

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
            create_task(task_id="rck4", file_path=str(fp), role="orchestrator_impl", checks=["gk-testing-octopoid"])
            update_task_queue("rck4", "provisional", commits_count=2)
            record_check_result("rck4", "gk-testing-octopoid", "fail", "Tests broken")

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
            "checks": ["gk-testing-octopoid"],
            "check_results": {"gk-testing-octopoid": {"status": "pass", "summary": "OK"}},
        }

        result = _format_task(task)
        assert result["checks"] == ["gk-testing-octopoid"]
        assert result["check_results"]["gk-testing-octopoid"]["status"] == "pass"


# ---------------------------------------------------------------------------
# Pre-check: skip tasks with pending checks
# ---------------------------------------------------------------------------


class TestPreCheckSkipsPendingChecks:
    """Tests that the pre-check skips tasks with pending gatekeeper checks."""

    def test_pre_check_skips_task_with_pending_checks(self, mock_config, initialized_db):
        """Pre-check should not reject/accept tasks that have pending gatekeeper checks."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.roles.pre_check.is_db_enabled", return_value=True):
                with patch("orchestrator.queue_utils.is_db_enabled", return_value=True):
                    with patch("orchestrator.queue_utils.get_queue_dir", return_value=mock_config / "shared" / "queue"):
                        with patch("orchestrator.roles.pre_check.get_pre_check_config", return_value={
                            "require_commits": True,
                            "max_attempts_before_planning": 3,
                            "claim_timeout_minutes": 60,
                        }):
                            with patch.dict(os.environ, {
                                "AGENT_NAME": "test-pre-check",
                                "AGENT_ID": "0",
                                "AGENT_ROLE": "pre_check",
                                "PARENT_PROJECT": str(mock_config.parent),
                                "WORKTREE": str(mock_config.parent),
                                "SHARED_DIR": str(mock_config / "shared"),
                                "ORCHESTRATOR_DIR": str(mock_config),
                            }):
                                from orchestrator.roles.pre_check import PreCheckRole
                                from orchestrator.db import (
                                    create_task, claim_task, submit_completion, get_task,
                                )

                                create_task(
                                    task_id="vskip1",
                                    file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-vskip1.md"),
                                    role="orchestrator_impl",
                                    checks=["gk-testing-octopoid"],
                                )
                                claim_task()
                                submit_completion("vskip1", commits_count=3, turns_used=20)

                                prov_dir = mock_config / "shared" / "queue" / "provisional"
                                prov_dir.mkdir(parents=True, exist_ok=True)
                                (prov_dir / "TASK-vskip1.md").write_text(
                                    "# [TASK-vskip1] Orch task\nROLE: orchestrator_impl\n"
                                )

                                role = PreCheckRole()
                                result = role.run()
                                assert result == 0

                                task = get_task("vskip1")
                                assert task["queue"] == "provisional", (
                                    f"Pre-check should skip task with pending checks, "
                                    f"but task was moved to {task['queue']}"
                                )

    def test_pre_check_processes_task_after_checks_pass(self, mock_config, initialized_db):
        """After checks pass, pre-check should accept the task normally."""
        with patch("orchestrator.db.get_database_path", return_value=initialized_db):
            with patch("orchestrator.roles.pre_check.is_db_enabled", return_value=True):
                with patch("orchestrator.queue_utils.is_db_enabled", return_value=True):
                    with patch("orchestrator.queue_utils.get_queue_dir", return_value=mock_config / "shared" / "queue"):
                        with patch("orchestrator.roles.pre_check.get_pre_check_config", return_value={
                            "require_commits": True,
                            "max_attempts_before_planning": 3,
                            "claim_timeout_minutes": 60,
                        }):
                            with patch.dict(os.environ, {
                                "AGENT_NAME": "test-pre-check",
                                "AGENT_ID": "0",
                                "AGENT_ROLE": "pre_check",
                                "PARENT_PROJECT": str(mock_config.parent),
                                "WORKTREE": str(mock_config.parent),
                                "SHARED_DIR": str(mock_config / "shared"),
                                "ORCHESTRATOR_DIR": str(mock_config),
                            }):
                                from orchestrator.roles.pre_check import PreCheckRole
                                from orchestrator.db import (
                                    create_task, claim_task, submit_completion,
                                    record_check_result, get_task,
                                )

                                create_task(
                                    task_id="vskip2",
                                    file_path=str(mock_config / "shared" / "queue" / "provisional" / "TASK-vskip2.md"),
                                    role="orchestrator_impl",
                                    checks=["gk-testing-octopoid"],
                                )
                                claim_task()
                                submit_completion("vskip2", commits_count=3, turns_used=20)
                                record_check_result("vskip2", "gk-testing-octopoid", "pass", "OK")

                                prov_dir = mock_config / "shared" / "queue" / "provisional"
                                prov_dir.mkdir(parents=True, exist_ok=True)
                                (prov_dir / "TASK-vskip2.md").write_text(
                                    "# [TASK-vskip2] Orch task\nROLE: orchestrator_impl\n"
                                )

                                role = PreCheckRole()
                                result = role.run()
                                assert result == 0

                                task = get_task("vskip2")
                                assert task["queue"] == "done"


# ---------------------------------------------------------------------------
# Schema migration test
# ---------------------------------------------------------------------------


class TestSchemaMigrationV6:
    """Tests for the v5->v6 migration adding check_results column."""

    def test_migrate_from_v5_adds_check_results(self, mock_config, db_path):
        """Migrating from v5 to v6 adds the check_results column."""
        with patch("orchestrator.db.get_database_path", return_value=db_path):
            from orchestrator.db import init_schema, get_connection, SCHEMA_VERSION

            init_schema()

            with get_connection() as conn:
                cursor = conn.execute("PRAGMA table_info(tasks)")
                columns = {row["name"]: row for row in cursor.fetchall()}
                assert "check_results" in columns

            assert SCHEMA_VERSION >= 6


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
                        checks=["custom-gatekeeper"],
                    )

                    task_id = task_path.stem.replace("TASK-", "")
                    task = get_task(task_id)

                    assert task["checks"] == ["custom-gatekeeper"]
