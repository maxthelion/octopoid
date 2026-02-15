"""Tests for orchestrator.queue_utils module."""

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
        task_path = mock_orchestrator_dir / "runtime" / "shared" / "queue" / "incoming" / "TASK-minimal.md"
        task_path.write_text("# [TASK-minimal] Minimal task\n\nSome content")

        task = parse_task_file(task_path)

        assert task["priority"] == "P2"  # default
        assert task["branch"] == "main"  # default
        assert task["role"] is None


class TestQueueOperationsFileBased:
    """Tests for queue operations via SDK."""

    def test_count_queue_empty(self, mock_config):
        """Test counting an empty queue."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.list.return_value = []

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            from orchestrator.queue_utils import count_queue

            count = count_queue("incoming")
            assert count == 0

    def test_count_queue_with_tasks(self, mock_config, sample_task_file):
        """Test counting queue with tasks."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.list.return_value = [
            {"id": "abc12345", "title": "Implement feature X", "priority": "P1"},
        ]

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            from orchestrator.queue_utils import count_queue

            count = count_queue("incoming")
            assert count == 1

    def test_list_tasks_empty(self, mock_config):
        """Test listing empty queue."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.list.return_value = []

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            from orchestrator.queue_utils import list_tasks

            tasks = list_tasks("incoming")
            assert tasks == []

    def test_list_tasks_sorted_by_priority(self, mock_orchestrator_dir):
        """Test that tasks are sorted by priority."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.list.return_value = [
            {"id": "p2", "title": "P2 task", "priority": "P2", "created_at": "2024-01-15T10:02:00"},
            {"id": "p0", "title": "P0 task", "priority": "P0", "created_at": "2024-01-15T10:00:00"},
            {"id": "p1", "title": "P1 task", "priority": "P1", "created_at": "2024-01-15T10:01:00"},
        ]

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            from orchestrator.queue_utils import list_tasks

            tasks = list_tasks("incoming")

            assert tasks[0]["id"] == "p0"
            assert tasks[1]["id"] == "p1"
            assert tasks[2]["id"] == "p2"


class TestClaimTask:
    """Tests for claim_task function."""

    def test_claim_task_via_sdk(self, mock_orchestrator_dir, sample_task_file):
        """Test claiming a task via SDK."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.claim.return_value = {
            "id": "abc12345",
            "title": "Implement feature X",
            "queue": "claimed",
            "file_path": "TASK-abc12345.md",
        }

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            with patch('orchestrator.queue_utils.get_orchestrator_id', return_value="test-orch"):
                with patch('orchestrator.queue_utils.get_queue_limits', return_value={"max_claimed": 5}):
                    # Point resolve_task_file to the sample file
                    with patch('orchestrator.queue_utils.resolve_task_file', return_value=sample_task_file):
                        from orchestrator.queue_utils import claim_task

                        task = claim_task(agent_name="test-agent")

                        assert task is not None
                        assert task["id"] == "abc12345"
                        mock_sdk.tasks.claim.assert_called_once()

    def test_claim_task_with_role_filter(self, mock_orchestrator_dir):
        """Test claiming with role filter passes role_filter to SDK."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.claim.return_value = {
            "id": "impl1",
            "title": "Impl",
            "role": "implement",
            "queue": "claimed",
        }

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            with patch('orchestrator.queue_utils.get_orchestrator_id', return_value="test-orch"):
                with patch('orchestrator.queue_utils.get_queue_limits', return_value={"max_claimed": 5}):
                    from orchestrator.queue_utils import claim_task

                    task = claim_task(role_filter="implement")

                    assert task["id"] == "impl1"
                    # Verify role_filter was passed to SDK
                    call_kwargs = mock_sdk.tasks.claim.call_args[1]
                    assert call_kwargs["role_filter"] == "implement"

    def test_claim_task_no_available(self, mock_config):
        """Test claiming when no tasks available."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.claim.return_value = None

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            with patch('orchestrator.queue_utils.get_orchestrator_id', return_value="test-orch"):
                with patch('orchestrator.queue_utils.get_queue_limits', return_value={"max_claimed": 5}):
                    from orchestrator.queue_utils import claim_task

                    task = claim_task()
                    assert task is None

    def test_claim_task_passes_agent_name(self, mock_orchestrator_dir, sample_task_file):
        """Test that agent_name is passed to SDK claim call."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.claim.return_value = {
            "id": "abc12345",
            "title": "Implement feature X",
            "queue": "claimed",
        }

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            with patch('orchestrator.queue_utils.get_orchestrator_id', return_value="test-orch"):
                with patch('orchestrator.queue_utils.get_queue_limits', return_value={"max_claimed": 5}):
                    from orchestrator.queue_utils import claim_task

                    claim_task(agent_name="my-agent")

                    call_kwargs = mock_sdk.tasks.claim.call_args[1]
                    assert call_kwargs["agent_name"] == "my-agent"


class TestCompleteTask:
    """Tests for complete_task function."""

    def test_complete_task_via_sdk(self, mock_orchestrator_dir, sample_task_file):
        """Test completing a task via SDK."""
        mock_sdk = MagicMock()

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            with patch('orchestrator.queue_utils.cleanup_task_notes'):
                from orchestrator.queue_utils import complete_task

                result_path = complete_task(sample_task_file, result="Task completed successfully")

                # SDK accept should be called with task ID
                mock_sdk.tasks.accept.assert_called_once_with("abc12345", accepted_by="complete_task")

                # File should still exist and have metadata appended
                assert result_path.exists()
                content = result_path.read_text()
                assert "COMPLETED_AT:" in content
                assert "Task completed successfully" in content


class TestSubmitCompletion:
    """Tests for submit_completion function."""

    def test_submit_completion_via_sdk(self, mock_orchestrator_dir, sample_task_file):
        """Test that submit_completion calls SDK to submit task."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.get.return_value = {
            "id": "abc12345",
            "queue": "claimed",
            "attempt_count": 0,
            "rejection_count": 0,
        }

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            from orchestrator.queue_utils import submit_completion

            result_path = submit_completion(sample_task_file, commits_count=5, turns_used=30)

            # SDK submit should be called (with execution_notes generated)
            assert mock_sdk.tasks.submit.call_count == 1
            call_kwargs = mock_sdk.tasks.submit.call_args[1]
            assert call_kwargs["task_id"] == "abc12345"
            assert call_kwargs["commits_count"] == 5
            assert call_kwargs["turns_used"] == 30
            assert "execution_notes" in call_kwargs  # Generated by _generate_execution_notes

            # File should have metadata appended
            assert result_path is not None
            content = result_path.read_text()
            assert "SUBMITTED_AT:" in content
            assert "COMMITS_COUNT: 5" in content
            assert "TURNS_USED: 30" in content


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

    def test_fail_task_via_sdk(self, mock_orchestrator_dir, sample_task_file):
        """Test failing a task via SDK."""
        mock_sdk = MagicMock()

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            with patch('orchestrator.queue_utils.cleanup_task_worktree'):
                from orchestrator.queue_utils import fail_task

                result_path = fail_task(sample_task_file, error="Something went wrong")

                # SDK should update task to failed queue
                mock_sdk.tasks.update.assert_called_once_with("abc12345", queue="failed")

                # File should have error metadata appended
                content = result_path.read_text()
                assert "FAILED_AT:" in content
                assert "Something went wrong" in content

    def test_fail_task_truncates_long_error(self, mock_orchestrator_dir, sample_task_file):
        """A 10,000-char error should be truncated so the error section is <= 600 chars."""
        long_error = "X" * 10_000
        mock_sdk = MagicMock()

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            with patch('orchestrator.queue_utils.cleanup_task_worktree'):
                from orchestrator.queue_utils import fail_task

                result_path = fail_task(sample_task_file, error=long_error)

                content = result_path.read_text()
                assert "FAILED_AT:" in content

                # Extract just the error section
                error_start = content.find("## Error")
                assert error_start != -1, "Error section not found"
                error_section = content[error_start:]
                assert len(error_section) <= 600, (
                    f"Error section is {len(error_section)} chars, expected <= 600"
                )
                # Should end with truncation marker
                assert "..." in error_section


class TestRejectTask:
    """Tests for reject_task function."""

    def test_reject_task_via_sdk(self, mock_orchestrator_dir, sample_task_file):
        """Test rejecting a task via SDK."""
        mock_sdk = MagicMock()

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            from orchestrator.queue_utils import reject_task

            result_path = reject_task(
                sample_task_file,
                reason="already_implemented",
                details="This feature already exists",
                rejected_by="impl-agent",
            )

            # SDK should update task to rejected queue
            mock_sdk.tasks.update.assert_called_once_with("abc12345", queue="rejected")

            # File should have rejection metadata appended
            content = result_path.read_text()
            assert "REJECTION_REASON: already_implemented" in content
            assert "REJECTED_BY: impl-agent" in content


class TestRetryTask:
    """Tests for retry_task function."""

    def test_retry_task_via_sdk(self, mock_orchestrator_dir, sample_task_file):
        """Test retrying a failed task via SDK."""
        mock_sdk = MagicMock()

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            from orchestrator.queue_utils import retry_task

            result_path = retry_task(sample_task_file)

            # SDK should update task to incoming queue
            mock_sdk.tasks.update.assert_called_once_with(
                "abc12345", queue="incoming", claimed_by=None, claimed_at=None
            )

            # File should have retry metadata appended
            content = result_path.read_text()
            assert "RETRIED_AT:" in content


class TestGetQueueStatus:
    """Tests for get_queue_status function."""

    def test_get_queue_status(self, mock_orchestrator_dir, sample_task_file):
        """Test getting queue status via SDK."""
        mock_sdk = MagicMock()

        def mock_list_tasks(queue=None):
            if queue == "incoming":
                return [{"id": "abc12345", "title": "Task 1", "priority": "P1"}]
            return []

        mock_sdk.tasks.list.side_effect = mock_list_tasks

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            with patch('orchestrator.queue_utils.get_queue_limits', return_value={"max_incoming": 20, "max_claimed": 5, "max_open_prs": 10}):
                with patch('orchestrator.queue_utils.count_open_prs', return_value=2):
                    with patch('orchestrator.queue_utils.list_projects', return_value=[]):
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

    def test_get_task_by_id_via_sdk(self, mock_orchestrator_dir, sample_task_file):
        """Test getting a task by ID via SDK."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.get.return_value = {
            "id": "abc12345",
            "title": "Implement feature X",
            "queue": "incoming",
        }

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            from orchestrator.queue_utils import get_task_by_id

            task = get_task_by_id("abc12345")

            assert task is not None
            assert task["id"] == "abc12345"
            mock_sdk.tasks.get.assert_called_once_with("abc12345")

    def test_get_task_by_id_not_found(self, mock_config):
        """Test getting a non-existent task."""
        mock_sdk = MagicMock()
        mock_sdk.tasks.get.return_value = None

        with patch('orchestrator.queue_utils.get_sdk', return_value=mock_sdk):
            from orchestrator.queue_utils import get_task_by_id

            task = get_task_by_id("nonexistent")
            assert task is None


class TestBackpressure:
    """Tests for backpressure functions."""

    def test_can_create_task_within_limit(self, mock_config):
        """Test can_create_task when within limits."""
        with patch('orchestrator.queue_utils.get_queue_limits', return_value={"max_incoming": 20, "max_claimed": 5, "max_open_prs": 10}):
            with patch('orchestrator.queue_utils.count_queue', return_value=5):
                from orchestrator.queue_utils import can_create_task

                can_create, reason = can_create_task()

                assert can_create is True
                assert reason == ""

    def test_can_create_task_queue_full(self, mock_config):
        """Test can_create_task when queue is full."""
        with patch('orchestrator.queue_utils.get_queue_limits', return_value={"max_incoming": 20, "max_claimed": 5, "max_open_prs": 10}):
            with patch('orchestrator.queue_utils.count_queue', return_value=15):
                from orchestrator.queue_utils import can_create_task

                can_create, reason = can_create_task()

                assert can_create is False
                assert "Queue full" in reason

    def test_can_claim_task_no_tasks(self, mock_config):
        """Test can_claim_task when no tasks available."""
        with patch('orchestrator.queue_utils.get_queue_limits', return_value={"max_incoming": 20, "max_claimed": 5, "max_open_prs": 10}):
            with patch('orchestrator.queue_utils.count_queue', side_effect=[0, 0]):
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


class TestParseTaskFileChecks:
    """Tests for CHECKS: field parsing in parse_task_file."""

    def test_parse_task_with_checks(self, mock_orchestrator_dir):
        """parse_task_file extracts CHECKS field as a list."""
        from orchestrator.queue_utils import parse_task_file

        task_path = mock_orchestrator_dir / "runtime" / "shared" / "queue" / "incoming" / "TASK-chkparse1.md"
        task_path.write_text(
            "# [TASK-chkparse1] Task with checks\n"
            "ROLE: orchestrator_impl\n"
            "PRIORITY: P1\n"
            "CHECKS: gk-testing-octopoid,vitest\n"
            "\n## Context\nSome context\n"
        )

        task = parse_task_file(task_path)
        assert task["checks"] == ["gk-testing-octopoid", "vitest"]

    def test_parse_task_with_single_check(self, mock_orchestrator_dir):
        """parse_task_file handles a single check."""
        from orchestrator.queue_utils import parse_task_file

        task_path = mock_orchestrator_dir / "runtime" / "shared" / "queue" / "incoming" / "TASK-chkparse2.md"
        task_path.write_text(
            "# [TASK-chkparse2] Task with one check\n"
            "CHECKS: gk-testing-octopoid\n"
            "\n## Context\nSome context\n"
        )

        task = parse_task_file(task_path)
        assert task["checks"] == ["gk-testing-octopoid"]

    def test_parse_task_without_checks(self, mock_orchestrator_dir):
        """parse_task_file returns empty list when no CHECKS line."""
        from orchestrator.queue_utils import parse_task_file

        task_path = mock_orchestrator_dir / "runtime" / "shared" / "queue" / "incoming" / "TASK-chkparse3.md"
        task_path.write_text(
            "# [TASK-chkparse3] Task without checks\n"
            "ROLE: implement\n"
            "\n## Context\nSome context\n"
        )

        task = parse_task_file(task_path)
        assert task["checks"] == []

    def test_parse_task_checks_with_spaces(self, mock_orchestrator_dir):
        """parse_task_file handles spaces around commas in CHECKS."""
        from orchestrator.queue_utils import parse_task_file

        task_path = mock_orchestrator_dir / "runtime" / "shared" / "queue" / "incoming" / "TASK-chkparse4.md"
        task_path.write_text(
            "# [TASK-chkparse4] Task with spaced checks\n"
            "CHECKS: gk-testing-octopoid , vitest , typecheck\n"
            "\n## Context\nSome context\n"
        )

        task = parse_task_file(task_path)
        assert task["checks"] == ["gk-testing-octopoid", "vitest", "typecheck"]


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


class TestFindTaskFile:
    """Tests for find_task_file function."""

    def test_find_in_incoming(self, mock_orchestrator_dir):
        """find_task_file locates a task in incoming/."""
        incoming = mock_orchestrator_dir / "runtime" / "shared" / "queue" / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        task_path = incoming / "TASK-find001.md"
        task_path.write_text("# [TASK-find001] Test\n")

        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import find_task_file

            result = find_task_file("find001")
            assert result is not None
            assert result == task_path

    def test_find_in_escalated(self, mock_orchestrator_dir):
        """find_task_file locates a task in escalated/."""
        escalated = mock_orchestrator_dir / "runtime" / "shared" / "queue" / "escalated"
        escalated.mkdir(parents=True, exist_ok=True)
        task_path = escalated / "TASK-find002.md"
        task_path.write_text("# [TASK-find002] Test\n")

        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import find_task_file

            result = find_task_file("find002")
            assert result is not None
            assert result == task_path

    def test_find_in_done(self, mock_orchestrator_dir):
        """find_task_file locates a task in done/."""
        done = mock_orchestrator_dir / "runtime" / "shared" / "queue" / "done"
        done.mkdir(parents=True, exist_ok=True)
        task_path = done / "TASK-find003.md"
        task_path.write_text("# [TASK-find003] Test\n")

        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import find_task_file

            result = find_task_file("find003")
            assert result is not None
            assert result == task_path

    def test_find_in_recycled(self, mock_orchestrator_dir):
        """find_task_file locates a task in recycled/."""
        recycled = mock_orchestrator_dir / "runtime" / "shared" / "queue" / "recycled"
        recycled.mkdir(parents=True, exist_ok=True)
        task_path = recycled / "TASK-find004.md"
        task_path.write_text("# [TASK-find004] Test\n")

        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import find_task_file

            result = find_task_file("find004")
            assert result is not None
            assert result == task_path

    def test_find_not_found(self, mock_orchestrator_dir):
        """find_task_file returns None when task doesn't exist."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import find_task_file

            result = find_task_file("nonexistent")
            assert result is None

    def test_find_in_breakdown(self, mock_orchestrator_dir):
        """find_task_file locates a task in breakdown/."""
        breakdown = mock_orchestrator_dir / "runtime" / "shared" / "queue" / "breakdown"
        breakdown.mkdir(parents=True, exist_ok=True)
        task_path = breakdown / "TASK-find005.md"
        task_path.write_text("# [TASK-find005] Test\n")

        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import find_task_file

            result = find_task_file("find005")
            assert result is not None
            assert result == task_path


