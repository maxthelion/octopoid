"""Tests for per-task logging infrastructure.

These tests verify that:
- Task log files are created correctly
- All log_* functions write properly formatted entries
- Claim counting and time extraction work correctly
- Logs survive across task state transitions
"""

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator import task_logger


@pytest.fixture
def mock_task_logger(mock_config):
    """Ensure task logger uses the mock orchestrator directory."""
    logs_dir = mock_config / "logs" / "tasks"
    logs_dir.mkdir(parents=True, exist_ok=True)
    yield mock_config


class TestTaskLogger:
    """Tests for task_logger module."""

    def test_get_task_logs_dir_creates_directory(self, mock_task_logger):
        """get_task_logs_dir creates the logs directory if it doesn't exist."""
        # Remove the directory if it exists
        logs_dir = mock_task_logger / "logs" / "tasks"
        if logs_dir.exists():
            import shutil
            shutil.rmtree(logs_dir)

        # Call should create it
        result = task_logger.get_task_logs_dir()
        assert result.exists()
        assert result.is_dir()

    def test_get_task_log_path(self, mock_task_logger):
        """get_task_log_path returns correct path for task ID."""
        path = task_logger.get_task_log_path("abc12345")
        assert path.name == "TASK-abc12345.log"
        assert "logs/tasks" in str(path)

    def test_log_created(self, mock_task_logger):
        """log_created writes a properly formatted entry."""
        task_id = "test001"
        task_logger.log_created(
            task_id=task_id,
            created_by="human",
            priority="P1",
            role="implement",
            source="issue-123"
        )

        log_path = task_logger.get_task_log_path(task_id)
        assert log_path.exists()

        content = log_path.read_text()
        assert "CREATED" in content
        assert "by=human" in content
        assert "priority=P1" in content
        assert "role=implement" in content
        assert "source=issue-123" in content

    def test_log_claimed(self, mock_task_logger):
        """log_claimed writes attempt number correctly."""
        task_id = "test002"
        task_logger.log_claimed(task_id, "agent-1", attempt=1)
        task_logger.log_claimed(task_id, "agent-2", attempt=2)

        log_path = task_logger.get_task_log_path(task_id)
        content = log_path.read_text()

        lines = content.strip().split("\n")
        assert len(lines) == 2
        assert "CLAIMED" in lines[0]
        assert "by=agent-1" in lines[0]
        assert "attempt=1" in lines[0]
        assert "CLAIMED" in lines[1]
        assert "by=agent-2" in lines[1]
        assert "attempt=2" in lines[1]

    def test_log_submitted(self, mock_task_logger):
        """log_submitted writes commits and turns."""
        task_id = "test003"
        task_logger.log_submitted(task_id, commits=3, turns=42)

        log_path = task_logger.get_task_log_path(task_id)
        content = log_path.read_text()

        assert "SUBMITTED" in content
        assert "commits=3" in content
        assert "turns=42" in content

    def test_log_rejected(self, mock_task_logger):
        """log_rejected writes rejection reason and source."""
        task_id = "test004"
        task_logger.log_rejected(
            task_id,
            reason="No commits made",
            rejected_by="pre-check"
        )

        log_path = task_logger.get_task_log_path(task_id)
        content = log_path.read_text()

        assert "REJECTED" in content
        assert "rejected_by=pre-check" in content
        # Reason with spaces should be quoted
        assert 'reason="No commits made"' in content

    def test_log_accepted(self, mock_task_logger):
        """log_accepted writes PR number and reviewer."""
        task_id = "test005"
        task_logger.log_accepted(task_id, pr_number=42, reviewer="gatekeeper")

        log_path = task_logger.get_task_log_path(task_id)
        content = log_path.read_text()

        assert "ACCEPTED" in content
        assert "pr=42" in content
        assert "reviewer=gatekeeper" in content

    def test_log_failed(self, mock_task_logger):
        """log_failed writes failure reason."""
        task_id = "test006"
        task_logger.log_failed(
            task_id,
            reason="Agent crashed",
            failed_by="scheduler"
        )

        log_path = task_logger.get_task_log_path(task_id)
        content = log_path.read_text()

        assert "FAILED" in content
        assert 'reason="Agent crashed"' in content
        assert "failed_by=scheduler" in content

    def test_log_escalated(self, mock_task_logger):
        """log_escalated writes escalation info."""
        task_id = "test007"
        task_logger.log_escalated(
            task_id,
            reason="exceeded max attempts",
            escalated_by="scheduler"
        )

        log_path = task_logger.get_task_log_path(task_id)
        content = log_path.read_text()

        assert "ESCALATED" in content
        assert 'reason="exceeded max attempts"' in content
        assert "escalated_by=scheduler" in content

    def test_log_recycled(self, mock_task_logger):
        """log_recycled writes recycling info."""
        task_id = "test008"
        task_logger.log_recycled(
            task_id,
            recycled_by="recycler",
            reason="too_large"
        )

        log_path = task_logger.get_task_log_path(task_id)
        content = log_path.read_text()

        assert "RECYCLED" in content
        assert "recycled_by=recycler" in content
        assert "reason=too_large" in content

    def test_get_claim_count_zero_when_no_log(self, mock_task_logger):
        """get_claim_count returns 0 when log doesn't exist."""
        count = task_logger.get_claim_count("nonexistent")
        assert count == 0

    def test_get_claim_count_counts_claims(self, mock_task_logger):
        """get_claim_count correctly counts CLAIMED entries."""
        task_id = "test009"
        task_logger.log_created(task_id, "human", "P1", "implement")
        assert task_logger.get_claim_count(task_id) == 0

        task_logger.log_claimed(task_id, "agent-1", 1)
        assert task_logger.get_claim_count(task_id) == 1

        task_logger.log_submitted(task_id, 3, 50)
        assert task_logger.get_claim_count(task_id) == 1

        task_logger.log_claimed(task_id, "agent-2", 2)
        assert task_logger.get_claim_count(task_id) == 2

        task_logger.log_claimed(task_id, "agent-3", 3)
        assert task_logger.get_claim_count(task_id) == 3

    def test_get_claim_times_returns_none_when_no_log(self, mock_task_logger):
        """get_claim_times returns (None, None) when log doesn't exist."""
        first, last = task_logger.get_claim_times("nonexistent")
        assert first is None
        assert last is None

    def test_get_claim_times_extracts_timestamps(self, mock_task_logger):
        """get_claim_times extracts first and last claim timestamps."""
        task_id = "test010"
        task_logger.log_created(task_id, "human", "P1", "implement")

        # Add first claim
        task_logger.log_claimed(task_id, "agent-1", 1)
        first1, last1 = task_logger.get_claim_times(task_id)
        assert first1 is not None
        assert last1 is not None
        assert first1 == last1  # Only one claim

        # Parse to ensure valid ISO format
        datetime.fromisoformat(first1)

        # Add second claim (with a small delay to ensure different timestamp)
        import time
        time.sleep(0.01)
        task_logger.log_claimed(task_id, "agent-2", 2)
        first2, last2 = task_logger.get_claim_times(task_id)

        assert first2 == first1  # First claim unchanged
        assert last2 != first2   # Last claim is different
        assert last2 > first2    # Last claim is more recent

    def test_log_entries_have_timestamps(self, mock_task_logger):
        """All log entries have ISO8601 timestamps."""
        task_id = "test011"
        task_logger.log_created(task_id, "human", "P1", "implement")
        task_logger.log_claimed(task_id, "agent", 1)

        log_path = task_logger.get_task_log_path(task_id)
        content = log_path.read_text()

        lines = content.strip().split("\n")
        for line in lines:
            # Each line should start with [timestamp]
            assert line.startswith("[")
            assert "]" in line
            timestamp_str = line[1:line.index("]")]
            # Should be valid ISO format
            datetime.fromisoformat(timestamp_str)

    def test_log_lifecycle_complete(self, mock_task_logger):
        """Test a complete task lifecycle log."""
        task_id = "test012"

        # Create
        task_logger.log_created(task_id, "human", "P2", "implement")

        # Claim and work
        task_logger.log_claimed(task_id, "impl-agent", 1)
        task_logger.log_submitted(task_id, commits=5, turns=80)

        # Reject and retry
        task_logger.log_rejected(task_id, "Failing tests", "pre-check")
        task_logger.log_claimed(task_id, "impl-agent", 2)
        task_logger.log_submitted(task_id, commits=2, turns=25)

        # Accept
        task_logger.log_accepted(task_id, pr_number=123, reviewer="gatekeeper")

        # Verify log has all entries
        log_path = task_logger.get_task_log_path(task_id)
        content = log_path.read_text()

        assert "CREATED" in content
        assert content.count("CLAIMED") == 2
        assert content.count("SUBMITTED") == 2
        assert "REJECTED" in content
        assert "ACCEPTED" in content

        # Verify claim count
        assert task_logger.get_claim_count(task_id) == 2
