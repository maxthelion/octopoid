"""End-to-end tests for gatekeeper wiring into task submission lifecycle.

Tests the full flow: task creation → check assignment → check result recording →
scheduler processing → correct final state.  Also tests scheduler dispatch of
gatekeeper agents for non-mechanical checks.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestOrchestratorImplChecksAutoAssigned:
    """Verify that orchestrator_impl tasks do NOT get auto-assigned checks (self-merge runs pytest)."""

    def test_create_task_no_default_checks_for_orchestrator_impl(self, mock_config, initialized_db):
        """queue_utils.create_task does not auto-assign checks for orchestrator_impl role."""
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
                assert task["checks"] == []

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

                # Step 3: Gatekeeper records a passing result
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

                # Step 3: Gatekeeper records failure
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


class TestSchedulerGatekeeperDispatch:
    """Tests for scheduler dispatching gatekeeper agents for non-mechanical checks."""

    def _make_idle_state(self):
        """Create an idle AgentState for mocking."""
        from orchestrator.state_utils import AgentState
        return AgentState(running=False, pid=None, last_finished=None, last_exit_code=None, extra={})

    def test_dispatch_dispatches_gk_testing_octopoid(self, mock_config, initialized_db):
        """Scheduler dispatches gatekeeper for gk-testing-octopoid check."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.scheduler import dispatch_gatekeeper_agents

            # Task with gk-testing-octopoid check
            create_task(
                task_id="gk_dispatch",
                file_path="/gk_dispatch.md",
                role="orchestrator_impl",
                checks=["gk-testing-octopoid"],
                branch="main",
            )
            update_task_queue("gk_dispatch", "provisional", commits_count=2)

            idle_state = self._make_idle_state()

            with patch('orchestrator.scheduler.get_gatekeepers', return_value=[
                {"name": "gk-test", "role": "gatekeeper", "focus": "testing"},
            ]):
                with patch('orchestrator.scheduler.get_agents', return_value=[
                    {"name": "gk-test", "role": "gatekeeper", "focus": "testing"},
                ]):
                    with patch('orchestrator.scheduler.load_state', return_value=idle_state):
                        with patch('orchestrator.scheduler.is_overdue', return_value=True):
                            with patch('orchestrator.scheduler.spawn_agent', return_value=12345) as mock_spawn:
                                with patch('orchestrator.scheduler.save_state'):
                                    with patch('orchestrator.scheduler.ensure_worktree'):
                                        with patch('orchestrator.scheduler.setup_agent_commands'):
                                            with patch('orchestrator.scheduler.generate_agent_instructions'):
                                                with patch('orchestrator.scheduler.write_agent_env'):
                                                    with patch('orchestrator.scheduler.is_process_running', return_value=False):
                                                        dispatch_gatekeeper_agents()

                                                        # Should spawn — gk-testing-octopoid is now a gatekeeper check
                                                        mock_spawn.assert_called_once()
                                                        config = mock_spawn.call_args[0][3]
                                                        assert config["review_task_id"] == "gk_dispatch"
                                                        assert config["review_check_name"] == "gk-testing-octopoid"

    def test_dispatch_spawns_for_non_mechanical_check(self, mock_config, initialized_db):
        """Scheduler dispatches a gatekeeper for a non-mechanical check."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.scheduler import dispatch_gatekeeper_agents

            # Task with a non-mechanical check (e.g., "architecture-review")
            create_task(
                task_id="need_gk",
                file_path="/need_gk.md",
                role="implement",
                checks=["architecture-review"],
                branch="feature/test",
            )
            update_task_queue("need_gk", "provisional", commits_count=1)

            idle_state = self._make_idle_state()

            with patch('orchestrator.scheduler.get_gatekeepers', return_value=[
                {"name": "gk-arch", "role": "gatekeeper", "focus": "architecture"},
            ]):
                with patch('orchestrator.scheduler.get_agents', return_value=[
                    {"name": "gk-arch", "role": "gatekeeper", "focus": "architecture"},
                ]):
                    with patch('orchestrator.scheduler.load_state', return_value=idle_state):
                        with patch('orchestrator.scheduler.is_overdue', return_value=True):
                            with patch('orchestrator.scheduler.spawn_agent', return_value=12345) as mock_spawn:
                                with patch('orchestrator.scheduler.save_state'):
                                    with patch('orchestrator.scheduler.ensure_worktree'):
                                        with patch('orchestrator.scheduler.setup_agent_commands'):
                                            with patch('orchestrator.scheduler.generate_agent_instructions'):
                                                with patch('orchestrator.scheduler.write_agent_env'):
                                                    with patch('orchestrator.scheduler.is_process_running', return_value=False):
                                                        dispatch_gatekeeper_agents()

                                                        # Should spawn gatekeeper with review context
                                                        mock_spawn.assert_called_once()
                                                        call_args = mock_spawn.call_args
                                                        config = call_args[0][3]  # 4th positional arg is agent_config
                                                        assert config["review_task_id"] == "need_gk"
                                                        assert config["review_check_name"] == "architecture-review"

    def test_dispatch_sequential_only_first_pending(self, mock_config, initialized_db):
        """Scheduler only dispatches the first pending non-mechanical check per task."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.scheduler import dispatch_gatekeeper_agents

            # Task with two non-mechanical checks
            create_task(
                task_id="multi_check",
                file_path="/multi_check.md",
                role="implement",
                checks=["architecture-review", "qa-review"],
                branch="feature/test",
            )
            update_task_queue("multi_check", "provisional", commits_count=1)

            idle_state = self._make_idle_state()

            with patch('orchestrator.scheduler.get_gatekeepers', return_value=[
                {"name": "gk-1", "role": "gatekeeper", "focus": "architecture"},
                {"name": "gk-2", "role": "gatekeeper", "focus": "qa"},
            ]):
                with patch('orchestrator.scheduler.get_agents', return_value=[
                    {"name": "gk-1", "role": "gatekeeper", "focus": "architecture"},
                    {"name": "gk-2", "role": "gatekeeper", "focus": "qa"},
                ]):
                    with patch('orchestrator.scheduler.load_state', return_value=idle_state):
                        with patch('orchestrator.scheduler.is_overdue', return_value=True):
                            with patch('orchestrator.scheduler.spawn_agent', return_value=12345) as mock_spawn:
                                with patch('orchestrator.scheduler.save_state'):
                                    with patch('orchestrator.scheduler.ensure_worktree'):
                                        with patch('orchestrator.scheduler.setup_agent_commands'):
                                            with patch('orchestrator.scheduler.generate_agent_instructions'):
                                                with patch('orchestrator.scheduler.write_agent_env'):
                                                    with patch('orchestrator.scheduler.is_process_running', return_value=False):
                                                        dispatch_gatekeeper_agents()

                                                        # Should only spawn once — for the first check
                                                        assert mock_spawn.call_count == 1
                                                        call_args = mock_spawn.call_args
                                                        config = call_args[0][3]
                                                        assert config["review_check_name"] == "architecture-review"

    def test_dispatch_skips_already_completed_checks(self, mock_config, initialized_db):
        """Scheduler skips checks that already have results."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, record_check_result
            from orchestrator.scheduler import dispatch_gatekeeper_agents

            # Task with two checks, first already passed
            create_task(
                task_id="partial_done",
                file_path="/partial_done.md",
                role="implement",
                checks=["architecture-review", "qa-review"],
                branch="feature/test",
            )
            update_task_queue("partial_done", "provisional", commits_count=1)
            record_check_result("partial_done", "architecture-review", "pass", "Looks good")

            idle_state = self._make_idle_state()

            with patch('orchestrator.scheduler.get_gatekeepers', return_value=[
                {"name": "gk-1", "role": "gatekeeper", "focus": "qa"},
            ]):
                with patch('orchestrator.scheduler.get_agents', return_value=[
                    {"name": "gk-1", "role": "gatekeeper", "focus": "qa"},
                ]):
                    with patch('orchestrator.scheduler.load_state', return_value=idle_state):
                        with patch('orchestrator.scheduler.is_overdue', return_value=True):
                            with patch('orchestrator.scheduler.spawn_agent', return_value=12345) as mock_spawn:
                                with patch('orchestrator.scheduler.save_state'):
                                    with patch('orchestrator.scheduler.ensure_worktree'):
                                        with patch('orchestrator.scheduler.setup_agent_commands'):
                                            with patch('orchestrator.scheduler.generate_agent_instructions'):
                                                with patch('orchestrator.scheduler.write_agent_env'):
                                                    with patch('orchestrator.scheduler.is_process_running', return_value=False):
                                                        dispatch_gatekeeper_agents()

                                                        # Should dispatch for qa-review (architecture already done)
                                                        mock_spawn.assert_called_once()
                                                        config = mock_spawn.call_args[0][3]
                                                        assert config["review_check_name"] == "qa-review"

    def test_dispatch_skips_tasks_without_commits(self, mock_config, initialized_db):
        """Scheduler does not dispatch gatekeepers for tasks with 0 commits."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.scheduler import dispatch_gatekeeper_agents

            create_task(
                task_id="no_commits",
                file_path="/no_commits.md",
                role="implement",
                checks=["architecture-review"],
            )
            update_task_queue("no_commits", "provisional", commits_count=0)

            with patch('orchestrator.scheduler.get_gatekeepers', return_value=[
                {"name": "gk-1", "role": "gatekeeper"},
            ]):
                with patch('orchestrator.scheduler.spawn_agent') as mock_spawn:
                    dispatch_gatekeeper_agents()
                    mock_spawn.assert_not_called()

    def test_dispatch_skips_when_gatekeeper_already_active(self, mock_config, initialized_db):
        """Scheduler skips dispatch if a gatekeeper is already reviewing the task."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, upsert_agent
            from orchestrator.scheduler import dispatch_gatekeeper_agents

            create_task(
                task_id="active_review",
                file_path="/active_review.md",
                role="implement",
                checks=["architecture-review"],
                branch="feature/test",
            )
            update_task_queue("active_review", "provisional", commits_count=1)

            # Simulate a gatekeeper already running for this task
            upsert_agent("gk-arch", role="gatekeeper", running=True, pid=99999, current_task_id="active_review")

            with patch('orchestrator.scheduler.get_gatekeepers', return_value=[
                {"name": "gk-arch", "role": "gatekeeper", "focus": "architecture"},
            ]):
                with patch('orchestrator.scheduler.spawn_agent') as mock_spawn:
                    dispatch_gatekeeper_agents()
                    mock_spawn.assert_not_called()

    def test_dispatch_no_gatekeepers_configured(self, mock_config, initialized_db):
        """Scheduler gracefully handles no gatekeeper agents configured."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue
            from orchestrator.scheduler import dispatch_gatekeeper_agents

            create_task(
                task_id="no_gk_config",
                file_path="/no_gk_config.md",
                role="implement",
                checks=["architecture-review"],
            )
            update_task_queue("no_gk_config", "provisional", commits_count=1)

            with patch('orchestrator.scheduler.get_gatekeepers', return_value=[]):
                with patch('orchestrator.scheduler.spawn_agent') as mock_spawn:
                    dispatch_gatekeeper_agents()
                    mock_spawn.assert_not_called()


class TestAssignQaChecks:
    """Tests for auto-assigning gk-qa check to provisional app tasks with staging_url."""

    def test_assigns_gk_qa_to_app_task_with_staging_url(self, mock_config, initialized_db):
        """App task with staging_url gets gk-qa check auto-assigned."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, update_task, get_task
            from orchestrator.scheduler import assign_qa_checks

            create_task(
                task_id='qa_assign',
                file_path='/qa_assign.md',
                role='implement',
                branch='feature/test',
            )
            update_task_queue('qa_assign', 'provisional', commits_count=2)
            update_task(
                'qa_assign',
                staging_url='https://test.pages.dev',
            )

            assign_qa_checks()

            task = get_task('qa_assign')
            assert 'gk-qa' in task['checks']

    def test_skips_task_without_staging_url(self, mock_config, initialized_db):
        """Tasks without staging_url are skipped (deployment not ready)."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, get_task
            from orchestrator.scheduler import assign_qa_checks

            create_task(
                task_id='no_staging',
                file_path='/no_staging.md',
                role='implement',
                branch='feature/test',
            )
            update_task_queue('no_staging', 'provisional', commits_count=1)

            assign_qa_checks()

            task = get_task('no_staging')
            assert 'gk-qa' not in task['checks']

    def test_skips_orchestrator_impl_tasks(self, mock_config, initialized_db):
        """orchestrator_impl tasks are not assigned gk-qa (no visual QA for Python code)."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, update_task, get_task
            from orchestrator.scheduler import assign_qa_checks

            create_task(
                task_id='orch_task',
                file_path='/orch_task.md',
                role='orchestrator_impl',
            )
            update_task_queue('orch_task', 'provisional', commits_count=1)
            update_task(
                'orch_task',
                staging_url='https://test.pages.dev',
            )

            assign_qa_checks()

            task = get_task('orch_task')
            assert 'gk-qa' not in task['checks']

    def test_does_not_duplicate_gk_qa_check(self, mock_config, initialized_db):
        """If gk-qa is already in checks, it is not added again."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, update_task, get_task
            from orchestrator.scheduler import assign_qa_checks

            create_task(
                task_id='already_qa',
                file_path='/already_qa.md',
                role='implement',
                checks=['gk-qa'],
                branch='feature/test',
            )
            update_task_queue('already_qa', 'provisional', commits_count=1)
            update_task(
                'already_qa',
                staging_url='https://test.pages.dev',
            )

            assign_qa_checks()

            task = get_task('already_qa')
            # Should still be exactly one gk-qa, not duplicated
            assert task['checks'].count('gk-qa') == 1

    def test_preserves_existing_checks(self, mock_config, initialized_db):
        """Existing checks are preserved when gk-qa is added."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue, update_task, get_task
            from orchestrator.scheduler import assign_qa_checks

            create_task(
                task_id='has_checks',
                file_path='/has_checks.md',
                role='implement',
                checks=['architecture-review'],
                branch='feature/test',
            )
            update_task_queue('has_checks', 'provisional', commits_count=1)
            update_task(
                'has_checks',
                staging_url='https://test.pages.dev',
            )

            assign_qa_checks()

            task = get_task('has_checks')
            assert 'architecture-review' in task['checks']
            assert 'gk-qa' in task['checks']

    def test_skips_non_provisional_tasks(self, mock_config, initialized_db):
        """Only provisional tasks get gk-qa assigned."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task, get_task
            from orchestrator.scheduler import assign_qa_checks

            create_task(
                task_id='in_incoming',
                file_path='/in_incoming.md',
                role='implement',
            )
            update_task(
                'in_incoming',
                staging_url='https://test.pages.dev',
            )

            assign_qa_checks()

            task = get_task('in_incoming')
            # Task is in incoming, not provisional — should not get gk-qa
            assert 'gk-qa' not in task['checks']
