"""Tests for orchestrator.queue_utils module."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestParseTaskFile:
    """Tests for parse_task_file function."""

    def test_parse_valid_task(self, sample_task_file):
        """Test parsing a valid task file."""
        from orchestrator.queue_utils import parse_task_file

        task = parse_task_file(sample_task_file)

        assert task is not None
        assert task["id"] == "abc12345"
        assert task["title"] == "Implement feature X"
        assert task["role"] == "implement"
        assert task["priority"] == "P1"
        assert task["branch"] == "main"
        assert task["created_by"] == "human"
        assert "Feature X" in task["content"]

    def test_parse_task_with_dependencies(self, sample_task_with_dependencies):
        """Test parsing task with BLOCKED_BY field."""
        from orchestrator.queue_utils import parse_task_file

        task2 = parse_task_file(sample_task_with_dependencies["task2"])

        assert task2["blocked_by"] == "task0001"

    def test_parse_nonexistent_file(self, temp_dir):
        """Test parsing a non-existent file."""
        from orchestrator.queue_utils import parse_task_file

        result = parse_task_file(temp_dir / "nonexistent.md")
        assert result is None

    def test_parse_task_defaults(self, mock_orchestrator_dir):
        """Test default values for missing fields."""
        from orchestrator.queue_utils import parse_task_file

        # Create minimal task
        task_path = mock_orchestrator_dir / "shared" / "queue" / "incoming" / "TASK-minimal.md"
        task_path.write_text("# [TASK-minimal] Minimal task\n\nSome content")

        task = parse_task_file(task_path)

        assert task["priority"] == "P2"  # default
        assert task["branch"] == "main"  # default
        assert task["role"] is None


class TestQueueOperationsFileBased:
    """Tests for file-based queue operations."""

    def test_count_queue_empty(self, mock_config):
        """Test counting an empty queue."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            from orchestrator.queue_utils import count_queue

            count = count_queue("incoming")
            assert count == 0

    def test_count_queue_with_tasks(self, mock_config, sample_task_file):
        """Test counting queue with tasks."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            from orchestrator.queue_utils import count_queue

            count = count_queue("incoming")
            assert count == 1

    def test_list_tasks_empty(self, mock_config):
        """Test listing empty queue."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            from orchestrator.queue_utils import list_tasks

            tasks = list_tasks("incoming")
            assert tasks == []

    def test_list_tasks_sorted_by_priority(self, mock_orchestrator_dir):
        """Test that tasks are sorted by priority."""
        incoming_dir = mock_orchestrator_dir / "shared" / "queue" / "incoming"

        # Create tasks with different priorities
        (incoming_dir / "TASK-p2.md").write_text("# [TASK-p2] P2 task\nPRIORITY: P2\n")
        (incoming_dir / "TASK-p0.md").write_text("# [TASK-p0] P0 task\nPRIORITY: P0\n")
        (incoming_dir / "TASK-p1.md").write_text("# [TASK-p1] P1 task\nPRIORITY: P1\n")

        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
                from orchestrator.queue_utils import list_tasks

                tasks = list_tasks("incoming")

                assert tasks[0]["id"] == "p0"
                assert tasks[1]["id"] == "p1"
                assert tasks[2]["id"] == "p2"


class TestClaimTask:
    """Tests for claim_task function."""

    def test_claim_task_file_based(self, mock_orchestrator_dir, sample_task_file):
        """Test claiming a task in file-based mode."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
                from orchestrator.queue_utils import claim_task

                task = claim_task(agent_name="test-agent")

                assert task is not None
                assert task["id"] == "abc12345"
                # File should be moved to claimed
                assert "claimed" in str(task["path"])

    def test_claim_task_with_role_filter(self, mock_orchestrator_dir):
        """Test claiming with role filter."""
        incoming_dir = mock_orchestrator_dir / "shared" / "queue" / "incoming"
        (incoming_dir / "TASK-test1.md").write_text("# [TASK-test1] Test\nROLE: test\n")
        (incoming_dir / "TASK-impl1.md").write_text("# [TASK-impl1] Impl\nROLE: implement\n")

        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
                from orchestrator.queue_utils import claim_task

                task = claim_task(role_filter="implement")

                assert task["id"] == "impl1"

    def test_claim_task_no_available(self, mock_config):
        """Test claiming when no tasks available."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            from orchestrator.queue_utils import claim_task

            task = claim_task()
            assert task is None

    def test_claim_task_skips_blocked(self, mock_orchestrator_dir, sample_task_with_dependencies):
        """Test that blocked tasks are skipped in file mode."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
                from orchestrator.queue_utils import claim_task

                # Should claim task1 (unblocked), not task2 (blocked)
                task = claim_task()

                assert task["id"] == "task0001"


class TestCompleteTask:
    """Tests for complete_task function."""

    def test_complete_task_file_based(self, mock_orchestrator_dir, sample_task_file):
        """Test completing a task in file-based mode."""
        # First move to claimed
        claimed_dir = mock_orchestrator_dir / "shared" / "queue" / "claimed"
        claimed_path = claimed_dir / sample_task_file.name
        sample_task_file.rename(claimed_path)

        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
                from orchestrator.queue_utils import complete_task

                result_path = complete_task(claimed_path, result="Task completed successfully")

                assert "done" in str(result_path)
                assert result_path.exists()

                content = result_path.read_text()
                assert "COMPLETED_AT:" in content
                assert "Task completed successfully" in content


class TestSubmitCompletion:
    """Tests for submit_completion function."""

    def test_submit_completion_falls_back_in_file_mode(self, mock_orchestrator_dir, sample_task_file):
        """Test that submit_completion falls back to complete_task in file mode."""
        claimed_dir = mock_orchestrator_dir / "shared" / "queue" / "claimed"
        claimed_path = claimed_dir / sample_task_file.name
        sample_task_file.rename(claimed_path)

        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
                from orchestrator.queue_utils import submit_completion

                result_path = submit_completion(claimed_path, commits_count=5, turns_used=30)

                # Should go to done (not provisional) in file mode
                assert "done" in str(result_path)


class TestCreateTask:
    """Tests for create_task function."""

    def test_create_task_file_based(self, mock_orchestrator_dir):
        """Test creating a task in file-based mode."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
                from orchestrator.queue_utils import create_task

                task_path = create_task(
                    title="New feature",
                    role="implement",
                    context="Implement a new feature",
                    acceptance_criteria=["Feature works", "Tests pass"],
                    priority="P1",
                    branch="main",
                    created_by="test",
                )

                assert task_path.exists()
                content = task_path.read_text()

                assert "New feature" in content
                assert "ROLE: implement" in content
                assert "PRIORITY: P1" in content
                assert "- [ ] Feature works" in content

    def test_create_task_with_dependencies(self, mock_orchestrator_dir):
        """Test creating a task with dependencies."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
                from orchestrator.queue_utils import create_task

                task_path = create_task(
                    title="Dependent task",
                    role="implement",
                    context="This depends on another task",
                    acceptance_criteria=["Done"],
                    blocked_by="task123,task456",
                )

                content = task_path.read_text()
                assert "BLOCKED_BY: task123,task456" in content


class TestFailTask:
    """Tests for fail_task function."""

    def test_fail_task_file_based(self, mock_orchestrator_dir, sample_task_file):
        """Test failing a task in file-based mode."""
        claimed_dir = mock_orchestrator_dir / "shared" / "queue" / "claimed"
        claimed_path = claimed_dir / sample_task_file.name
        sample_task_file.rename(claimed_path)

        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
                from orchestrator.queue_utils import fail_task

                result_path = fail_task(claimed_path, error="Something went wrong")

                assert "failed" in str(result_path)
                content = result_path.read_text()
                assert "FAILED_AT:" in content
                assert "Something went wrong" in content


class TestRejectTask:
    """Tests for reject_task function."""

    def test_reject_task(self, mock_orchestrator_dir, sample_task_file):
        """Test rejecting a task."""
        claimed_dir = mock_orchestrator_dir / "shared" / "queue" / "claimed"
        claimed_path = claimed_dir / sample_task_file.name
        sample_task_file.rename(claimed_path)

        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
                from orchestrator.queue_utils import reject_task

                result_path = reject_task(
                    claimed_path,
                    reason="already_implemented",
                    details="This feature already exists",
                    rejected_by="impl-agent",
                )

                assert "rejected" in str(result_path)
                content = result_path.read_text()
                assert "REJECTION_REASON: already_implemented" in content
                assert "REJECTED_BY: impl-agent" in content


class TestRetryTask:
    """Tests for retry_task function."""

    def test_retry_task(self, mock_orchestrator_dir, sample_task_file):
        """Test retrying a failed task."""
        failed_dir = mock_orchestrator_dir / "shared" / "queue" / "failed"
        failed_path = failed_dir / sample_task_file.name
        sample_task_file.rename(failed_path)

        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
                from orchestrator.queue_utils import retry_task

                result_path = retry_task(failed_path)

                assert "incoming" in str(result_path)
                content = result_path.read_text()
                assert "RETRIED_AT:" in content


class TestGetQueueStatus:
    """Tests for get_queue_status function."""

    def test_get_queue_status(self, mock_orchestrator_dir, sample_task_file):
        """Test getting queue status."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
                with patch('orchestrator.queue_utils.get_queue_limits', return_value={"max_incoming": 20, "max_claimed": 5, "max_open_prs": 10}):
                    with patch('orchestrator.queue_utils.count_open_prs', return_value=2):
                        from orchestrator.queue_utils import get_queue_status

                        status = get_queue_status()

                        assert "incoming" in status
                        assert "claimed" in status
                        assert "done" in status
                        assert "limits" in status
                        assert status["incoming"]["count"] == 1
                        assert status["open_prs"] == 2


class TestGetTaskById:
    """Tests for get_task_by_id function."""

    def test_get_task_by_id_file_based(self, mock_orchestrator_dir, sample_task_file):
        """Test getting a task by ID in file mode."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "shared" / "queue"):
                from orchestrator.queue_utils import get_task_by_id

                task = get_task_by_id("abc12345")

                assert task is not None
                assert task["id"] == "abc12345"

    def test_get_task_by_id_not_found(self, mock_config):
        """Test getting a non-existent task."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            from orchestrator.queue_utils import get_task_by_id

            task = get_task_by_id("nonexistent")
            assert task is None


class TestBackpressure:
    """Tests for backpressure functions."""

    def test_can_create_task_within_limit(self, mock_config):
        """Test can_create_task when within limits."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_limits', return_value={"max_incoming": 20, "max_claimed": 5, "max_open_prs": 10}):
                with patch('orchestrator.queue_utils.count_queue', return_value=5):
                    from orchestrator.queue_utils import can_create_task

                    can_create, reason = can_create_task()

                    assert can_create is True
                    assert reason == ""

    def test_can_create_task_queue_full(self, mock_config):
        """Test can_create_task when queue is full."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_limits', return_value={"max_incoming": 20, "max_claimed": 5, "max_open_prs": 10}):
                with patch('orchestrator.queue_utils.count_queue', return_value=15):
                    from orchestrator.queue_utils import can_create_task

                    can_create, reason = can_create_task()

                    assert can_create is False
                    assert "Queue full" in reason

    def test_can_claim_task_no_tasks(self, mock_config):
        """Test can_claim_task when no tasks available."""
        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            with patch('orchestrator.queue_utils.get_queue_limits', return_value={"max_incoming": 20, "max_claimed": 5, "max_open_prs": 10}):
                with patch('orchestrator.queue_utils.count_queue', side_effect=[0, 0]):
                    from orchestrator.queue_utils import can_claim_task

                    can_claim, reason = can_claim_task()

                    assert can_claim is False
                    assert "No tasks" in reason
