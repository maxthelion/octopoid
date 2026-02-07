"""Tests for the agent notes system."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch


class TestNotesReadWrite:
    """Tests for get_task_notes / save_task_notes / cleanup_task_notes."""

    def test_save_and_read_notes(self, tmp_path):
        """save_task_notes creates a file that get_task_notes can read."""
        notes_dir = tmp_path / "shared" / "notes"

        with patch('orchestrator.config.get_notes_dir', return_value=notes_dir):
            from orchestrator.queue_utils import save_task_notes, get_task_notes

            save_task_notes("abc123", "impl-agent-1", "Found the test file at src/foo.ts", commits=0, turns=50)

            notes = get_task_notes("abc123")
            assert notes is not None
            assert "Attempt 1" in notes
            assert "impl-agent-1" in notes
            assert "Turns: 50" in notes
            assert "Commits: 0" in notes
            assert "Found the test file at src/foo.ts" in notes

    def test_notes_append_multiple_attempts(self, tmp_path):
        """Multiple saves append new attempt sections."""
        notes_dir = tmp_path / "shared" / "notes"

        with patch('orchestrator.config.get_notes_dir', return_value=notes_dir):
            from orchestrator.queue_utils import save_task_notes, get_task_notes

            save_task_notes("abc123", "impl-agent-1", "First attempt: explored codebase", commits=0, turns=50)
            save_task_notes("abc123", "impl-agent-1", "Second attempt: made progress", commits=1, turns=80)

            notes = get_task_notes("abc123")
            assert notes is not None
            assert "Attempt 1" in notes
            assert "Attempt 2" in notes
            assert "First attempt: explored codebase" in notes
            assert "Second attempt: made progress" in notes
            assert "Commits: 1" in notes

    def test_read_nonexistent_notes_returns_none(self, tmp_path):
        """get_task_notes returns None for a task with no notes."""
        notes_dir = tmp_path / "shared" / "notes"
        notes_dir.mkdir(parents=True)

        with patch('orchestrator.config.get_notes_dir', return_value=notes_dir):
            from orchestrator.queue_utils import get_task_notes

            assert get_task_notes("nonexistent") is None

    def test_notes_truncate_long_stdout(self, tmp_path):
        """Long stdout is truncated to the tail."""
        notes_dir = tmp_path / "shared" / "notes"

        with patch('orchestrator.config.get_notes_dir', return_value=notes_dir):
            from orchestrator.queue_utils import save_task_notes, get_task_notes, NOTES_STDOUT_LIMIT

            long_stdout = "x" * (NOTES_STDOUT_LIMIT + 5000)
            save_task_notes("abc123", "impl-agent-1", long_stdout, commits=0, turns=100)

            notes = get_task_notes("abc123")
            assert notes is not None
            assert "truncated" in notes
            # Should have at most NOTES_STDOUT_LIMIT chars of the actual output
            # plus header, metadata, and truncation notice
            assert len(notes) < NOTES_STDOUT_LIMIT + 500


class TestNotesCleanup:
    """Tests for cleanup_task_notes."""

    def test_cleanup_deletes_notes(self, tmp_path):
        """cleanup_task_notes removes the notes file."""
        notes_dir = tmp_path / "shared" / "notes"

        with patch('orchestrator.config.get_notes_dir', return_value=notes_dir):
            from orchestrator.queue_utils import save_task_notes, cleanup_task_notes, get_task_notes

            save_task_notes("abc123", "impl-agent-1", "Some notes", commits=0, turns=50)
            assert get_task_notes("abc123") is not None

            result = cleanup_task_notes("abc123")
            assert result is True
            assert get_task_notes("abc123") is None

    def test_cleanup_missing_file_noop(self, tmp_path):
        """cleanup_task_notes returns False for non-existent notes."""
        notes_dir = tmp_path / "shared" / "notes"
        notes_dir.mkdir(parents=True)

        with patch('orchestrator.config.get_notes_dir', return_value=notes_dir):
            from orchestrator.queue_utils import cleanup_task_notes

            result = cleanup_task_notes("nonexistent")
            assert result is False


class TestNotesIntegration:
    """Integration tests for notes cleanup during task lifecycle."""

    def test_accept_completion_cleans_notes(self, mock_config, sample_project_with_tasks):
        """accept_completion deletes notes for the accepted task."""
        db_path = sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"
        notes_dir = mock_config / "shared" / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)

        with patch('orchestrator.db.get_database_path', return_value=db_path):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    with patch('orchestrator.config.get_notes_dir', return_value=notes_dir):
                        from orchestrator.queue_utils import save_task_notes, get_task_notes, accept_completion

                        # Get a completed task that's in provisional
                        task = sample_project_with_tasks["completed_tasks"][0]
                        task_id = task["id"]

                        # Create notes for it
                        save_task_notes(task_id, "impl-agent-1", "Some exploration notes", commits=1, turns=50)
                        assert get_task_notes(task_id) is not None

                        # Accept the task
                        accept_completion(task["path"], validator="test")

                        # Notes should be cleaned up
                        assert get_task_notes(task_id) is None
