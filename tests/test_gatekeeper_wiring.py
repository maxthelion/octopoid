"""End-to-end tests for gatekeeper wiring into task submission lifecycle.

Tests the full flow: task creation → check assignment → check result recording →
scheduler processing → correct final state.
"""

import pytest
from unittest.mock import patch


class TestOrchestratorImplChecksAutoAssigned:
    """Verify that orchestrator_impl tasks get gk-testing-octopoid auto-assigned."""

    def test_create_task_assigns_gk_testing_for_orchestrator_impl(self, mock_config, initialized_db):
        """queue_utils.create_task auto-assigns checks for orchestrator_impl role."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.queue_utils.get_orchestrator_dir', return_value=mock_config):
                from orchestrator.queue_utils import create_task as qu_create_task

                path = qu_create_task(
                    title="Test orchestrator task",
                    context="Test context",
                    acceptance_criteria=["Tests pass"],
                    role="orchestrator_impl",
                    branch="main",
                )

                # Extract task ID from filename
                import re
                task_id = re.search(r"TASK-(\w+)", str(path)).group(1)

                from orchestrator.db import get_task
                task = get_task(task_id)

                assert task is not None
                assert "gk-testing-octopoid" in task["checks"]

    def test_create_task_no_checks_for_regular_implement(self, mock_config, initialized_db):
        """Regular implement tasks don't get auto-assigned checks."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.queue_utils.get_orchestrator_dir', return_value=mock_config):
                from orchestrator.queue_utils import create_task as qu_create_task

                path = qu_create_task(
                    title="Test regular task",
                    context="Test context",
                    acceptance_criteria=["Feature works"],
                    role="implement",
                    branch="feature/test",
                )

                import re
                task_id = re.search(r"TASK-(\w+)", str(path)).group(1)

                from orchestrator.db import get_task
                task = get_task(task_id)

                assert task is not None
                assert task["checks"] == []


class TestFullCheckLifecycle:
    """End-to-end test: submit → check recorded → scheduler processes → correct state."""

    def test_task_with_passing_check_reaches_human_review(self, mock_config, initialized_db):
        """Full lifecycle: create task → record pass → scheduler leaves in provisional."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.config.get_gatekeeper_config', return_value={
                'max_rejections': 3,
            }):
                from orchestrator.db import (
                    create_task, update_task_queue, record_check_result, get_task,
                )
                from orchestrator.scheduler import process_gatekeeper_reviews

                # Step 1: Create task with checks
                create_task(
                    task_id="e2e_pass",
                    file_path="/e2e_pass.md",
                    role="orchestrator_impl",
                    checks=["gk-testing-octopoid"],
                )

                # Step 2: Simulate agent completing work → task moves to provisional
                update_task_queue("e2e_pass", "provisional", commits_count=3)

                task = get_task("e2e_pass")
                assert task["queue"] == "provisional"
                assert task["checks"] == ["gk-testing-octopoid"]

                # Step 3: check_runner records a passing result
                record_check_result("e2e_pass", "gk-testing-octopoid", "pass", "All tests pass")

                # Step 4: Scheduler processes gatekeeper reviews
                process_gatekeeper_reviews()

                # Step 5: Verify task stays in provisional (awaiting human review)
                task = get_task("e2e_pass")
                assert task["queue"] == "provisional"
                check_results = task["check_results"]
                assert check_results["gk-testing-octopoid"]["status"] == "pass"

    def test_task_with_failing_check_gets_rejected(self, mock_config, initialized_db):
        """Full lifecycle: create task → record fail → scheduler rejects."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.config.get_gatekeeper_config', return_value={
                'max_rejections': 3,
            }):
                from orchestrator.db import (
                    create_task, update_task_queue, record_check_result, get_task,
                )
                from orchestrator.scheduler import process_gatekeeper_reviews

                # Create task file for review_reject_task
                prov_dir = mock_config / "shared" / "queue" / "provisional"
                prov_dir.mkdir(parents=True, exist_ok=True)
                incoming_dir = mock_config / "shared" / "queue" / "incoming"
                incoming_dir.mkdir(parents=True, exist_ok=True)

                task_path = prov_dir / "TASK-e2e_fail.md"
                task_path.write_text("# [TASK-e2e_fail] Test task\n\nROLE: orchestrator_impl\n")

                # Step 1: Create task
                create_task(
                    task_id="e2e_fail",
                    file_path=str(task_path),
                    role="orchestrator_impl",
                    checks=["gk-testing-octopoid"],
                )

                # Step 2: Move to provisional
                update_task_queue("e2e_fail", "provisional", commits_count=1)

                # Step 3: check_runner records failure
                record_check_result(
                    "e2e_fail", "gk-testing-octopoid", "fail",
                    "3 tests failing: test_foo, test_bar, test_baz"
                )

                # Step 4: Scheduler processes
                process_gatekeeper_reviews()

                # Step 5: Task should be rejected back to incoming
                task = get_task("e2e_fail")
                assert task["queue"] == "incoming"
                assert task["rejection_count"] == 1

    def test_task_without_checks_goes_straight_to_human_review(self, mock_config, initialized_db):
        """Tasks without checks skip gatekeeper entirely and stay in provisional."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.config.get_gatekeeper_config', return_value={
                'max_rejections': 3,
            }):
                from orchestrator.db import create_task, update_task_queue, get_task
                from orchestrator.scheduler import process_gatekeeper_reviews

                create_task(
                    task_id="e2e_nocheck",
                    file_path="/e2e_nocheck.md",
                    role="implement",
                )
                update_task_queue("e2e_nocheck", "provisional", commits_count=2)

                process_gatekeeper_reviews()

                task = get_task("e2e_nocheck")
                assert task["queue"] == "provisional"


class TestDashboardCheckDisplay:
    """Verify that reports.py correctly splits provisional tasks by check status."""

    def test_task_with_pending_checks_shows_as_checking(self, mock_config, initialized_db):
        """Task with unresolved checks appears in 'checking' section."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.reports import _gather_work

            with patch('orchestrator.queue_utils.get_orchestrator_dir', return_value=mock_config):
                create_task(
                    task_id="dash_pending",
                    file_path="/dash_pending.md",
                    checks=["gk-testing-octopoid"],
                )
                update_task_queue("dash_pending", "provisional", commits_count=1)

                work = _gather_work()

                checking_ids = [t["id"] for t in work["checking"]]
                review_ids = [t["id"] for t in work["in_review"]]

                assert "dash_pending" in checking_ids
                assert "dash_pending" not in review_ids

    def test_task_with_all_checks_passed_shows_as_in_review(self, mock_config, initialized_db):
        """Task with all checks passed appears in 'in_review' section."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, record_check_result
            from orchestrator.reports import _gather_work

            with patch('orchestrator.queue_utils.get_orchestrator_dir', return_value=mock_config):
                create_task(
                    task_id="dash_passed",
                    file_path="/dash_passed.md",
                    checks=["gk-testing-octopoid"],
                )
                update_task_queue("dash_passed", "provisional", commits_count=2)
                record_check_result("dash_passed", "gk-testing-octopoid", "pass", "All good")

                work = _gather_work()

                checking_ids = [t["id"] for t in work["checking"]]
                review_ids = [t["id"] for t in work["in_review"]]

                assert "dash_passed" not in checking_ids
                assert "dash_passed" in review_ids

    def test_task_without_checks_shows_as_in_review(self, mock_config, initialized_db):
        """Task with no checks defined appears directly in 'in_review' section."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.reports import _gather_work

            with patch('orchestrator.queue_utils.get_orchestrator_dir', return_value=mock_config):
                create_task(
                    task_id="dash_nocheck",
                    file_path="/dash_nocheck.md",
                )
                update_task_queue("dash_nocheck", "provisional", commits_count=1)

                work = _gather_work()

                review_ids = [t["id"] for t in work["in_review"]]
                assert "dash_nocheck" in review_ids
