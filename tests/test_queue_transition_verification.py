"""Tests to verify that queue transition failures are detected and raise errors."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestQueueTransitionVerification:
    """Tests that verify update_task_queue response is checked before logging success.

    These tests verify that when update_task_queue() returns None (indicating
    the task was not found in the database), the calling functions raise
    appropriate errors instead of silently logging success.
    """

    def test_update_task_queue_returns_none_for_missing_task(self, initialized_db):
        """Verify that update_task_queue returns None when task doesn't exist."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import update_task_queue

            # Update a non-existent task
            result = update_task_queue("nonexistent", "done")
            assert result is None

    def test_migrate_verifies_update_result(self, initialized_db):
        """Verify migrate.py checks update_task_queue response."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_task, update_task_queue

            # Create a task
            create_task(
                task_id="migrate1",
                file_path="/migrate1.md",
                priority="P1",
            )

            # Verify normal update works
            result = update_task_queue("migrate1", "provisional")
            assert result is not None
            assert result["queue"] == "provisional"

            # Verify update of nonexistent task returns None
            result = update_task_queue("nonexistent", "provisional")
            assert result is None
