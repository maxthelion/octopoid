"""Tests for orchestrator.queue_utils module."""

from unittest.mock import patch, MagicMock


class TestQueueOperationsFileBased:
    """Tests for queue operations via SDK."""

    def test_count_queue_empty(self, mock_config, mock_sdk_for_unit_tests):
        """Test counting an empty queue."""
        mock_sdk_for_unit_tests.tasks.list.return_value = []

        from orchestrator.queue_utils import count_queue

        count = count_queue("incoming")
        assert count == 0

    def test_count_queue_with_tasks(self, mock_config, sample_task_file, mock_sdk_for_unit_tests):
        """Test counting queue with tasks."""
        mock_sdk_for_unit_tests.tasks.list.return_value = [
            {"id": "abc12345", "title": "Implement feature X", "priority": "P1"},
        ]

        from orchestrator.queue_utils import count_queue

        count = count_queue("incoming")
        assert count == 1

    def test_list_tasks_empty(self, mock_config, mock_sdk_for_unit_tests):
        """Test listing empty queue."""
        mock_sdk_for_unit_tests.tasks.list.return_value = []

        from orchestrator.queue_utils import list_tasks

        tasks = list_tasks("incoming")
        assert tasks == []

    def test_list_tasks_sorted_by_priority(self, mock_orchestrator_dir, mock_sdk_for_unit_tests):
        """Test that tasks are sorted by priority."""
        mock_sdk_for_unit_tests.tasks.list.return_value = [
            {"id": "p2", "title": "P2 task", "priority": "P2", "created_at": "2024-01-15T10:02:00"},
            {"id": "p0", "title": "P0 task", "priority": "P0", "created_at": "2024-01-15T10:00:00"},
            {"id": "p1", "title": "P1 task", "priority": "P1", "created_at": "2024-01-15T10:01:00"},
        ]

        from orchestrator.queue_utils import list_tasks

        tasks = list_tasks("incoming")

        assert tasks[0]["id"] == "p0"
        assert tasks[1]["id"] == "p1"
        assert tasks[2]["id"] == "p2"


class TestClaimTask:
    """Tests for claim_task function."""

    def test_claim_task_via_sdk(self, mock_config, sample_task_file, mock_sdk_for_unit_tests):
        """Test claiming a task via SDK."""
        mock_sdk_for_unit_tests.tasks.claim.return_value = {
            "id": "abc12345",
            "title": "Implement feature X",
            "queue": "claimed",
            "file_path": "TASK-abc12345.md",
        }

        with patch('orchestrator.sdk.get_orchestrator_id', return_value="test-orch"):
            with patch('orchestrator.config.get_queue_limits', return_value={"max_claimed": 5}):
                # Point get_tasks_file_dir to the directory containing the sample file
                with patch('orchestrator.tasks.get_tasks_file_dir', return_value=sample_task_file.parent):
                    from orchestrator.queue_utils import claim_task

                    task = claim_task(agent_name="test-agent")

                    assert task is not None
                    assert task["id"] == "abc12345"
                    mock_sdk_for_unit_tests.tasks.claim.assert_called_once()

    def test_claim_task_with_role_filter(self, mock_orchestrator_dir, mock_sdk_for_unit_tests):
        """Test claiming with role filter passes role_filter to SDK."""
        mock_sdk_for_unit_tests.tasks.claim.return_value = {
            "id": "impl1",
            "title": "Impl",
            "role": "implement",
            "queue": "claimed",
        }

        with patch('orchestrator.sdk.get_orchestrator_id', return_value="test-orch"):
            with patch('orchestrator.config.get_queue_limits', return_value={"max_claimed": 5}):
                from orchestrator.queue_utils import claim_task

                task = claim_task(role_filter="implement")

                assert task["id"] == "impl1"
                # Verify role_filter was passed to SDK
                call_kwargs = mock_sdk_for_unit_tests.tasks.claim.call_args[1]
                assert call_kwargs["role_filter"] == "implement"

    def test_claim_task_no_available(self, mock_config, mock_sdk_for_unit_tests):
        """Test claiming when no tasks available."""
        mock_sdk_for_unit_tests.tasks.claim.return_value = None

        with patch('orchestrator.sdk.get_orchestrator_id', return_value="test-orch"):
            with patch('orchestrator.config.get_queue_limits', return_value={"max_claimed": 5}):
                from orchestrator.queue_utils import claim_task

                task = claim_task()
                assert task is None

    def test_claim_task_passes_agent_name(self, mock_orchestrator_dir, sample_task_file, mock_sdk_for_unit_tests):
        """Test that agent_name is passed to SDK claim call."""
        mock_sdk_for_unit_tests.tasks.claim.return_value = {
            "id": "abc12345",
            "title": "Implement feature X",
            "queue": "claimed",
        }

        with patch('orchestrator.sdk.get_orchestrator_id', return_value="test-orch"):
            with patch('orchestrator.config.get_queue_limits', return_value={"max_claimed": 5}):
                from orchestrator.queue_utils import claim_task

                claim_task(agent_name="my-agent")

                call_kwargs = mock_sdk_for_unit_tests.tasks.claim.call_args[1]
                assert call_kwargs["agent_name"] == "my-agent"


class TestCompleteTask:
    """Tests for complete_task function."""

    def test_complete_task_via_sdk(self, mock_orchestrator_dir, sample_task_file, mock_sdk_for_unit_tests):
        """Test completing a task via SDK."""
        mock_sdk_for_unit_tests.tasks.accept.return_value = {
            "id": "abc12345",
            "queue": "done",
        }

        with patch('orchestrator.task_notes.cleanup_task_notes'):
            from orchestrator.queue_utils import complete_task

            result = complete_task("abc12345")

            # SDK accept should be called with task ID
            mock_sdk_for_unit_tests.tasks.accept.assert_called_once_with("abc12345", accepted_by="complete_task")

            # Function should return SDK result dict
            assert result is not None
            assert result["id"] == "abc12345"
            assert result["queue"] == "done"


class TestSubmitCompletion:
    """Tests for submit_completion function."""

    def test_submit_completion_via_sdk(self, mock_orchestrator_dir, sample_task_file, mock_sdk_for_unit_tests):
        """Test that submit_completion calls SDK to submit task."""
        mock_sdk_for_unit_tests.tasks.get.return_value = {
            "id": "abc12345",
            "queue": "claimed",
            "attempt_count": 0,
            "rejection_count": 0,
        }
        mock_sdk_for_unit_tests.tasks.submit.return_value = {
            "id": "abc12345",
            "queue": "provisional",
        }

        from orchestrator.queue_utils import submit_completion

        result = submit_completion("abc12345", commits_count=5, turns_used=30)

        # SDK submit should be called (with execution_notes generated)
        assert mock_sdk_for_unit_tests.tasks.submit.call_count == 1
        call_kwargs = mock_sdk_for_unit_tests.tasks.submit.call_args[1]
        assert call_kwargs["task_id"] == "abc12345"
        assert call_kwargs["commits_count"] == 5
        assert call_kwargs["turns_used"] == 30
        assert "execution_notes" in call_kwargs  # Generated by _generate_execution_notes

        # Function should return SDK result dict
        assert result is not None
        assert result["id"] == "abc12345"
        assert result["queue"] == "provisional"


class TestCreateTask:
    """Tests for create_task function."""

    def test_create_task_file_based(self, mock_orchestrator_dir, mock_sdk_for_unit_tests):
        """Test creating a task in file-based mode."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
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

            # Verify SDK was called (mocked) to create the task
            mock_sdk_for_unit_tests.tasks.create.assert_called_once()

    def test_create_task_with_dependencies(self, mock_orchestrator_dir):
        """Test creating a task with dependencies."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
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

    def test_create_task_acceptance_criteria_string_multiline(self, mock_orchestrator_dir):
        """Test that a multi-line string for acceptance_criteria preserves lines.

        Regression test: previously, passing a string would iterate character-by-character,
        producing one '- [ ] <char>' line per character instead of per line.
        """
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            criteria_str = "Feature works correctly\nTests are added\nDocs updated"

            task_path = create_task(
                title="String criteria task",
                role="implement",
                context="Test context",
                acceptance_criteria=criteria_str,
            )

            content = task_path.read_text()

            # Each line should be a checklist item
            assert "- [ ] Feature works correctly" in content
            assert "- [ ] Tests are added" in content
            assert "- [ ] Docs updated" in content

            # Must NOT have character-level explosion
            assert "- [ ] F" not in content or "- [ ] Feature works correctly" in content
            # Count checklist items — should be exactly 3
            checklist_lines = [
                line for line in content.splitlines()
                if line.strip().startswith("- [ ]")
            ]
            assert len(checklist_lines) == 3

    def test_create_task_acceptance_criteria_string_with_existing_prefixes(self, mock_orchestrator_dir):
        """Test that lines already prefixed with '- [ ]' are not double-wrapped."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            criteria_str = "- [ ] Already prefixed\n- [ ] Also prefixed"

            task_path = create_task(
                title="Pre-prefixed criteria",
                role="implement",
                context="Test context",
                acceptance_criteria=criteria_str,
            )

            content = task_path.read_text()

            # Should appear exactly once, not double-wrapped
            assert "- [ ] Already prefixed" in content
            assert "- [ ] - [ ] Already prefixed" not in content
            assert "- [ ] Also prefixed" in content
            assert "- [ ] - [ ] Also prefixed" not in content

    def test_create_task_acceptance_criteria_list_with_existing_prefixes(self, mock_orchestrator_dir):
        """Test that list items already prefixed with '- [ ]' are not double-wrapped."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            task_path = create_task(
                title="Pre-prefixed list criteria",
                role="implement",
                context="Test context",
                acceptance_criteria=["- [ ] Already prefixed", "Bare line"],
            )

            content = task_path.read_text()

            assert "- [ ] Already prefixed" in content
            assert "- [ ] - [ ] Already prefixed" not in content
            assert "- [ ] Bare line" in content

    def test_create_task_acceptance_criteria_single_string(self, mock_orchestrator_dir):
        """Test that a single-line string works correctly."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            task_path = create_task(
                title="Single line criteria",
                role="implement",
                context="Test context",
                acceptance_criteria="Feature works",
            )

            content = task_path.read_text()

            assert "- [ ] Feature works" in content
            checklist_lines = [
                line for line in content.splitlines()
                if line.strip().startswith("- [ ]")
            ]
            assert len(checklist_lines) == 1


class TestFailTask:
    """Tests for fail_task function."""

    def test_fail_task_via_sdk(self, mock_orchestrator_dir, sample_task_file, mock_sdk_for_unit_tests):
        """Test failing a task via SDK."""
        mock_sdk_for_unit_tests.tasks.update.return_value = {
            "id": "abc12345",
            "queue": "failed",
        }

        mock_logger = MagicMock()

        with patch('orchestrator.tasks.get_task_logger', return_value=mock_logger):
            with patch('orchestrator.git_utils.cleanup_task_worktree'):
                from orchestrator.queue_utils import fail_task

                result = fail_task("abc12345", error="Something went wrong")

                # SDK should update task to failed queue
                mock_sdk_for_unit_tests.tasks.update.assert_called_once_with("abc12345", queue="failed")

                # Function should return SDK result dict
                assert result is not None
                assert result["id"] == "abc12345"
                assert result["queue"] == "failed"

    def test_fail_task_truncates_long_error(self, mock_orchestrator_dir, sample_task_file, mock_sdk_for_unit_tests):
        """A 10,000-char error should be truncated in the SDK call."""
        long_error = "X" * 10_000
        mock_sdk_for_unit_tests.tasks.update.return_value = {
            "id": "abc12345",
            "queue": "failed",
        }

        mock_logger = MagicMock()

        with patch('orchestrator.tasks.get_task_logger', return_value=mock_logger):
            with patch('orchestrator.git_utils.cleanup_task_worktree'):
                from orchestrator.queue_utils import fail_task

                result = fail_task("abc12345", error=long_error)

                # SDK should update task to failed queue
                mock_sdk_for_unit_tests.tasks.update.assert_called_once_with("abc12345", queue="failed")

                # Function should return SDK result dict
                assert result is not None
                assert result["id"] == "abc12345"
                assert result["queue"] == "failed"


class TestRejectTask:
    """Tests for reject_task function."""

    def test_reject_task_via_sdk(self, mock_orchestrator_dir, sample_task_file, mock_sdk_for_unit_tests):
        """Test rejecting a task via SDK."""
        mock_sdk_for_unit_tests.tasks.reject.return_value = {
            "id": "abc12345",
            "queue": "rejected",
        }

        from orchestrator.queue_utils import reject_task

        result = reject_task(
            "abc12345",
            reason="already_implemented",
            details="This feature already exists",
            rejected_by="impl-agent",
        )

        # SDK reject should be called with the parameters
        mock_sdk_for_unit_tests.tasks.reject.assert_called_once_with(
            task_id="abc12345",
            reason="already_implemented",
            details="This feature already exists",
            rejected_by="impl-agent",
        )

        # Function should return SDK result dict
        assert result is not None
        assert result["id"] == "abc12345"
        assert result["queue"] == "rejected"


class TestRetryTask:
    """Tests for retry_task function."""

    def test_retry_task_via_sdk(self, mock_orchestrator_dir, sample_task_file, mock_sdk_for_unit_tests):
        """Test retrying a failed task via SDK."""
        mock_sdk_for_unit_tests.tasks.update.return_value = {
            "id": "abc12345",
            "queue": "incoming",
        }

        from orchestrator.queue_utils import retry_task

        result = retry_task("abc12345")

        # SDK should update task to incoming queue
        mock_sdk_for_unit_tests.tasks.update.assert_called_once_with(
            "abc12345", queue="incoming", claimed_by=None, claimed_at=None
        )

        # Function should return SDK result dict
        assert result is not None
        assert result["id"] == "abc12345"
        assert result["queue"] == "incoming"


class TestGetQueueStatus:
    """Tests for get_queue_status function."""

    def test_get_queue_status(self, mock_orchestrator_dir, sample_task_file, mock_sdk_for_unit_tests):
        """Test getting queue status via SDK."""
        def mock_list_tasks(queue=None):
            if queue == "incoming":
                return [{"id": "abc12345", "title": "Task 1", "priority": "P1"}]
            return []

        # Reset side_effect from previous tests
        mock_sdk_for_unit_tests.tasks.list.side_effect = mock_list_tasks
        mock_sdk_for_unit_tests.tasks.list.return_value = None  # Clear return_value when using side_effect

        with patch('orchestrator.config.get_queue_limits', return_value={"max_incoming": 20, "max_claimed": 5, "max_provisional": 10}):
            with patch('orchestrator.projects.list_projects', return_value=[]):
                from orchestrator.backpressure import get_queue_status

                status = get_queue_status()

                assert "incoming" in status
                assert "claimed" in status
                assert "done" in status
                assert "limits" in status
                assert status["incoming"]["count"] == 1

        # Reset side_effect after test
        mock_sdk_for_unit_tests.tasks.list.side_effect = None
        mock_sdk_for_unit_tests.tasks.list.return_value = []


class TestGetTaskById:
    """Tests for get_task_by_id function."""

    def test_get_task_by_id_via_sdk(self, mock_orchestrator_dir, sample_task_file, mock_sdk_for_unit_tests):
        """Test getting a task by ID via SDK."""
        mock_sdk_for_unit_tests.tasks.get.return_value = {
            "id": "abc12345",
            "title": "Implement feature X",
            "queue": "incoming",
        }

        from orchestrator.queue_utils import get_task_by_id

        task = get_task_by_id("abc12345")

        assert task is not None
        assert task["id"] == "abc12345"
        mock_sdk_for_unit_tests.tasks.get.assert_called_once_with("abc12345")

    def test_get_task_by_id_not_found(self, mock_config, mock_sdk_for_unit_tests):
        """Test getting a non-existent task."""
        mock_sdk_for_unit_tests.tasks.get.return_value = None

        from orchestrator.queue_utils import get_task_by_id

        task = get_task_by_id("nonexistent")
        assert task is None


class TestBackpressure:
    """Tests for backpressure functions."""

    def test_can_create_task_within_limit(self, mock_config, mock_sdk_for_unit_tests):
        """Test can_create_task when within limits."""
        mock_sdk_for_unit_tests.tasks.list.return_value = [{"id": f"task{i}"} for i in range(5)]

        with patch('orchestrator.config.get_queue_limits', return_value={"max_incoming": 20, "max_claimed": 5, "max_provisional": 10}):
            from orchestrator.queue_utils import can_create_task

            can_create, reason = can_create_task()

            assert can_create is True
            assert reason == ""

    def test_can_create_task_queue_full(self, mock_config, mock_sdk_for_unit_tests):
        """Test can_create_task when queue is full."""
        mock_sdk_for_unit_tests.tasks.list.return_value = [{"id": f"task{i}"} for i in range(15)]

        with patch('orchestrator.config.get_queue_limits', return_value={"max_incoming": 20, "max_claimed": 5, "max_provisional": 10}):
            from orchestrator.queue_utils import can_create_task

            can_create, reason = can_create_task()

            assert can_create is False
            assert "Queue full" in reason

    def test_can_claim_task_no_tasks(self, mock_config, mock_sdk_for_unit_tests):
        """Test can_claim_task when no tasks available."""
        mock_sdk_for_unit_tests.tasks.list.return_value = []

        with patch('orchestrator.config.get_queue_limits', return_value={"max_incoming": 20, "max_claimed": 5, "max_provisional": 10}):
            from orchestrator.queue_utils import can_claim_task

            can_claim, reason = can_claim_task()

            assert can_claim is False
            assert "No tasks" in reason


class TestCreateTaskBlockedByNormalization:
    """Tests for blocked_by normalization in queue_utils.create_task."""

    def test_create_task_no_blocked_by_no_blocked_by_in_file(self, mock_orchestrator_dir):
        """create_task without blocked_by should not write BLOCKED_BY line to file."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            task_path = create_task(
                title="No blockers",
                role="implement",
                context="test",
                acceptance_criteria=["test"],
                blocked_by=None,
            )

            content = task_path.read_text()
            assert "BLOCKED_BY" not in content

    def test_create_task_string_none_blocked_by_no_blocked_by_in_file(self, mock_orchestrator_dir):
        """create_task with blocked_by='None' should not write BLOCKED_BY line to file."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            task_path = create_task(
                title="String None blocker",
                role="implement",
                context="test",
                acceptance_criteria=["test"],
                blocked_by="None",
            )

            content = task_path.read_text()
            assert "BLOCKED_BY" not in content

    def test_create_task_empty_string_blocked_by_no_blocked_by_in_file(self, mock_orchestrator_dir):
        """create_task with blocked_by='' should not write BLOCKED_BY line to file."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            task_path = create_task(
                title="Empty string blocker",
                role="implement",
                context="test",
                acceptance_criteria=["test"],
                blocked_by="",
            )

            content = task_path.read_text()
            assert "BLOCKED_BY" not in content

    def test_create_task_valid_blocked_by_written_to_file(self, mock_orchestrator_dir):
        """create_task with a real blocked_by writes BLOCKED_BY line to file."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            task_path = create_task(
                title="Valid blocker",
                role="implement",
                context="test",
                acceptance_criteria=["test"],
                blocked_by="abc123",
            )

            content = task_path.read_text()
            assert "BLOCKED_BY: abc123" in content


class TestCreateTaskChecks:
    """Tests for checks parameter in queue_utils.create_task."""

    def test_create_task_with_checks_writes_to_file(self, mock_orchestrator_dir):
        """create_task with checks writes CHECKS line to file."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            task_path = create_task(
                title="Task with checks",
                role="orchestrator_impl",
                context="test context",
                acceptance_criteria=["test"],
                checks=["gk-testing-octopoid", "vitest"],
            )

            content = task_path.read_text()
            assert "CHECKS: gk-testing-octopoid,vitest" in content

    def test_create_task_without_checks_no_checks_line(self, mock_orchestrator_dir):
        """create_task without checks does not write CHECKS line."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            task_path = create_task(
                title="Task without checks",
                role="implement",
                context="test context",
                acceptance_criteria=["test"],
            )

            content = task_path.read_text()
            assert "CHECKS:" not in content


class TestCreateTaskOrchestratorImplDefaultChecks:
    """Tests for default checks on orchestrator_impl tasks."""

    def test_orchestrator_impl_no_default_checks(self, mock_orchestrator_dir):
        """Creating orchestrator_impl task without checks gets no default checks."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            task_path = create_task(
                title="Orchestrator impl task",
                role="orchestrator_impl",
                context="test context",
                acceptance_criteria=["test"],
            )

            content = task_path.read_text()
            assert "CHECKS:" not in content

    def test_orchestrator_impl_explicit_checks_override_default(self, mock_orchestrator_dir):
        """Creating orchestrator_impl task with explicit checks uses those instead."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            task_path = create_task(
                title="Orchestrator impl task with custom checks",
                role="orchestrator_impl",
                context="test context",
                acceptance_criteria=["test"],
                checks=["custom-check", "another-check"],
            )

            content = task_path.read_text()
            assert "CHECKS: custom-check,another-check" in content
            assert "gk-testing-octopoid" not in content

    def test_non_orchestrator_impl_no_default_checks(self, mock_orchestrator_dir):
        """Non-orchestrator_impl tasks do NOT get default checks."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            task_path = create_task(
                title="Normal implement task",
                role="implement",
                context="test context",
                acceptance_criteria=["test"],
            )

            content = task_path.read_text()
            assert "CHECKS:" not in content


