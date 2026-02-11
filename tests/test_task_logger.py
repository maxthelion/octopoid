"""Tests for orchestrator.task_logger module."""

import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


class TestTaskLoggerBasics:
    """Tests for TaskLogger basic functionality."""

    def test_get_task_log_dir_creates_directory(self, mock_config):
        """Test that get_task_log_dir creates the logs directory."""
        from orchestrator.task_logger import get_task_log_dir

        log_dir = get_task_log_dir()
        assert log_dir.exists()
        assert log_dir.name == "tasks"
        assert log_dir.parent.name == "logs"

    def test_get_task_log_path(self, mock_config):
        """Test getting the path to a task log file."""
        from orchestrator.task_logger import get_task_log_path

        log_path = get_task_log_path("abc12345")
        assert log_path.name == "TASK-abc12345.log"
        assert log_path.parent.name == "tasks"

    def test_task_logger_init(self, mock_config):
        """Test TaskLogger initialization."""
        from orchestrator.task_logger import TaskLogger

        logger = TaskLogger("test123")
        assert logger.task_id == "test123"
        assert logger.log_path.name == "TASK-test123.log"


class TestTaskLoggerEvents:
    """Tests for TaskLogger event logging methods."""

    def test_log_created(self, mock_config):
        """Test logging task creation."""
        from orchestrator.task_logger import TaskLogger

        logger = TaskLogger("create123")
        logger.log_created(
            created_by="human",
            priority="P1",
            role="implement",
            queue="incoming"
        )

        # Read log file
        content = logger.log_path.read_text()
        assert "CREATED" in content
        assert "by=human" in content
        assert "priority=P1" in content
        assert "role=implement" in content
        assert "queue=incoming" in content
        # Check timestamp format
        assert content.startswith("[")
        assert "]" in content

    def test_log_claimed(self, mock_config):
        """Test logging task claim."""
        from orchestrator.task_logger import TaskLogger

        logger = TaskLogger("claim123")
        logger.log_claimed(
            claimed_by="orch-impl-1",
            attempt=2,
            from_queue="incoming"
        )

        content = logger.log_path.read_text()
        assert "CLAIMED" in content
        assert "by=orch-impl-1" in content
        assert "attempt=2" in content
        assert "from_queue=incoming" in content

    def test_log_submitted(self, mock_config):
        """Test logging task submission."""
        from orchestrator.task_logger import TaskLogger

        logger = TaskLogger("submit123")
        logger.log_submitted(commits=3, turns=42)

        content = logger.log_path.read_text()
        assert "SUBMITTED" in content
        assert "commits=3" in content
        assert "turns=42" in content

    def test_log_accepted(self, mock_config):
        """Test logging task acceptance."""
        from orchestrator.task_logger import TaskLogger

        logger = TaskLogger("accept123")
        logger.log_accepted(accepted_by="gatekeeper")

        content = logger.log_path.read_text()
        assert "ACCEPTED" in content
        assert "by=gatekeeper" in content

    def test_log_rejected(self, mock_config):
        """Test logging task rejection."""
        from orchestrator.task_logger import TaskLogger

        logger = TaskLogger("reject123")
        logger.log_rejected(
            reason="No commits made",
            rejected_by="pre-check"
        )

        content = logger.log_path.read_text()
        assert "REJECTED" in content
        assert "No commits made" in content
        assert "by=pre-check" in content

    def test_log_rejected_truncates_long_reason(self, mock_config):
        """Test that long rejection reasons are truncated."""
        from orchestrator.task_logger import TaskLogger

        logger = TaskLogger("reject_long")
        long_reason = "x" * 200
        logger.log_rejected(reason=long_reason, rejected_by="test")

        content = logger.log_path.read_text()
        assert "..." in content  # Truncation marker
        assert len(content) < len(long_reason)  # Shorter than original

    def test_log_failed(self, mock_config):
        """Test logging task failure."""
        from orchestrator.task_logger import TaskLogger

        logger = TaskLogger("fail123")
        logger.log_failed(error="Python traceback here")

        content = logger.log_path.read_text()
        assert "FAILED" in content
        assert "Python traceback here" in content

    def test_multiple_events_append(self, mock_config):
        """Test that multiple events append to the same log file."""
        from orchestrator.task_logger import TaskLogger

        logger = TaskLogger("multi123")
        logger.log_created(created_by="human", priority="P1", role="implement")
        logger.log_claimed(claimed_by="agent1", attempt=1)
        logger.log_submitted(commits=2, turns=30)
        logger.log_accepted(accepted_by="gatekeeper")

        content = logger.log_path.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 4
        assert "CREATED" in lines[0]
        assert "CLAIMED" in lines[1]
        assert "SUBMITTED" in lines[2]
        assert "ACCEPTED" in lines[3]


class TestTaskLogParsing:
    """Tests for parsing task log files."""

    def test_parse_task_log_empty(self, mock_config):
        """Test parsing non-existent log file."""
        from orchestrator.task_logger import parse_task_log

        events = parse_task_log("nonexistent")
        assert events == []

    def test_parse_task_log_single_event(self, mock_config):
        """Test parsing a log with a single event."""
        from orchestrator.task_logger import TaskLogger, parse_task_log

        logger = TaskLogger("parse1")
        logger.log_created(created_by="human", priority="P2", role="test")

        events = parse_task_log("parse1")
        assert len(events) == 1
        assert events[0]["event"] == "CREATED"
        assert events[0]["by"] == "human"
        assert events[0]["priority"] == "P2"
        assert events[0]["role"] == "test"
        assert "timestamp" in events[0]

    def test_parse_task_log_multiple_events(self, mock_config):
        """Test parsing a log with multiple events."""
        from orchestrator.task_logger import TaskLogger, parse_task_log

        logger = TaskLogger("parse2")
        logger.log_created(created_by="human", priority="P1", role="implement")
        logger.log_claimed(claimed_by="agent1", attempt=1, from_queue="incoming")
        logger.log_rejected(reason="Test failure", rejected_by="pre-check")
        logger.log_claimed(claimed_by="agent2", attempt=2, from_queue="incoming")
        logger.log_submitted(commits=5, turns=80)

        events = parse_task_log("parse2")
        assert len(events) == 5
        assert events[0]["event"] == "CREATED"
        assert events[1]["event"] == "CLAIMED"
        assert events[1]["attempt"] == "1"
        assert events[2]["event"] == "REJECTED"
        assert events[3]["event"] == "CLAIMED"
        assert events[3]["attempt"] == "2"
        assert events[4]["event"] == "SUBMITTED"

    def test_parse_task_log_quoted_values(self, mock_config):
        """Test parsing quoted values like rejection reasons."""
        from orchestrator.task_logger import TaskLogger, parse_task_log

        logger = TaskLogger("parse_quoted")
        logger.log_rejected(reason="No commits made", rejected_by="pre-check")

        events = parse_task_log("parse_quoted")
        assert len(events) == 1
        assert "No commits made" in events[0]["reason"]


class TestTaskLogHelpers:
    """Tests for helper functions that read task logs."""

    def test_get_claim_count_no_claims(self, mock_config):
        """Test claim count for task that was never claimed."""
        from orchestrator.task_logger import TaskLogger, get_claim_count

        logger = TaskLogger("noclaims")
        logger.log_created(created_by="human", priority="P1", role="implement")

        count = get_claim_count("noclaims")
        assert count == 0

    def test_get_claim_count_single_claim(self, mock_config):
        """Test claim count for task claimed once."""
        from orchestrator.task_logger import TaskLogger, get_claim_count

        logger = TaskLogger("oneclaim")
        logger.log_created(created_by="human", priority="P1", role="implement")
        logger.log_claimed(claimed_by="agent1", attempt=1)

        count = get_claim_count("oneclaim")
        assert count == 1

    def test_get_claim_count_multiple_claims(self, mock_config):
        """Test claim count for task claimed multiple times."""
        from orchestrator.task_logger import TaskLogger, get_claim_count

        logger = TaskLogger("multiclaim")
        logger.log_created(created_by="human", priority="P1", role="implement")
        logger.log_claimed(claimed_by="agent1", attempt=1)
        logger.log_rejected(reason="Test", rejected_by="test")
        logger.log_claimed(claimed_by="agent2", attempt=2)
        logger.log_rejected(reason="Test", rejected_by="test")
        logger.log_claimed(claimed_by="agent3", attempt=3)

        count = get_claim_count("multiclaim")
        assert count == 3

    def test_get_first_claim_time(self, mock_config):
        """Test getting first claim timestamp."""
        from orchestrator.task_logger import TaskLogger, get_first_claim_time

        logger = TaskLogger("firstclaim")
        logger.log_created(created_by="human", priority="P1", role="implement")
        logger.log_claimed(claimed_by="agent1", attempt=1)
        logger.log_rejected(reason="Test", rejected_by="test")
        logger.log_claimed(claimed_by="agent2", attempt=2)

        first_claim = get_first_claim_time("firstclaim")
        assert first_claim is not None
        assert isinstance(first_claim, datetime)

    def test_get_last_claim_time(self, mock_config):
        """Test getting last claim timestamp."""
        from orchestrator.task_logger import TaskLogger, get_last_claim_time
        import time

        logger = TaskLogger("lastclaim")
        logger.log_created(created_by="human", priority="P1", role="implement")
        logger.log_claimed(claimed_by="agent1", attempt=1)
        time.sleep(0.01)  # Small delay to ensure different timestamps
        logger.log_claimed(claimed_by="agent2", attempt=2)

        last_claim = get_last_claim_time("lastclaim")
        first_claim = get_first_claim_time("lastclaim")

        assert last_claim is not None
        assert isinstance(last_claim, datetime)
        assert last_claim >= first_claim  # Last should be >= first

    def test_get_claim_times_no_claims(self, mock_config):
        """Test getting claim times when task was never claimed."""
        from orchestrator.task_logger import (
            TaskLogger,
            get_first_claim_time,
            get_last_claim_time,
        )

        logger = TaskLogger("noclaim_times")
        logger.log_created(created_by="human", priority="P1", role="implement")

        first = get_first_claim_time("noclaim_times")
        last = get_last_claim_time("noclaim_times")

        assert first is None
        assert last is None


class TestTaskLoggerIntegration:
    """Integration tests with queue operations."""

    def test_create_task_logs_creation(self, mock_config):
        """Test that create_task logs the CREATED event."""
        from orchestrator.queue_utils import create_task
        from orchestrator.task_logger import parse_task_log
        from unittest.mock import patch

        with patch('orchestrator.queue_utils.is_db_enabled', return_value=False):
            task_path = create_task(
                title="Test task",
                role="implement",
                context="Test context",
                acceptance_criteria=["Criterion 1"],
                priority="P1",
                created_by="test_user"
            )

            # Extract task ID from filename
            task_id = task_path.stem.replace("TASK-", "")

            # Check log was created
            events = parse_task_log(task_id)
            assert len(events) >= 1
            assert events[0]["event"] == "CREATED"
            assert events[0]["by"] == "test_user"
            assert events[0]["priority"] == "P1"
            assert events[0]["role"] == "implement"
