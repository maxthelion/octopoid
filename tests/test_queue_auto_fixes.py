"""Tests for queue manager auto-fixes (Phase 2)."""

import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

# Import system path setup
import sys
SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR.parent / ".orchestrator" / "scripts"))

from diagnose_queue_health import (
    fix_file_db_mismatch,
    fix_orphan_file,
    fix_stale_errors,
    parse_task_metadata,
)
from orchestrator import db
from orchestrator.queue_manager_logging import QueueManagerLogger


@pytest.fixture
def temp_dirs(mock_config, initialized_db):
    """Set up temporary directories for testing."""
    orchestrator_dir = mock_config
    queue_dir = orchestrator_dir / "shared" / "queue"
    logs_dir = orchestrator_dir / "logs"
    notes_dir = orchestrator_dir / "shared" / "notes"

    logs_dir.mkdir(parents=True, exist_ok=True)
    notes_dir.mkdir(parents=True, exist_ok=True)

    yield {
        'orchestrator_dir': orchestrator_dir,
        'queue_dir': queue_dir,
        'logs_dir': logs_dir,
        'notes_dir': notes_dir,
        'db_path': initialized_db,
    }


def test_parse_task_metadata_valid(temp_dirs):
    """Test parsing valid task file metadata."""
    queue_dir = temp_dirs['queue_dir']
    task_file = queue_dir / "incoming" / "TASK-abc123.md"

    content = """# [TASK-abc123] Test Task

ROLE: implement
PRIORITY: P1
BRANCH: main
CREATED: 2026-02-10T12:00:00
CREATED_BY: human

## Context
Test context

## Acceptance Criteria
- [ ] Test criterion
"""
    task_file.write_text(content)

    metadata = parse_task_metadata(task_file)

    assert metadata is not None
    assert metadata["id"] == "abc123"
    assert metadata["title"] == "Test Task"
    assert metadata["role"] == "implement"
    assert metadata["priority"] == "P1"
    assert metadata["branch"] == "main"
    assert metadata["created"] == "2026-02-10T12:00:00"
    assert metadata["created_by"] == "human"


def test_parse_task_metadata_invalid(temp_dirs):
    """Test parsing invalid task file returns None."""
    queue_dir = temp_dirs['queue_dir']
    task_file = queue_dir / "incoming" / "TASK-invalid.md"

    # Missing required fields
    content = """# Some random content
Without proper metadata
"""
    task_file.write_text(content)

    metadata = parse_task_metadata(task_file)
    assert metadata is None


def test_fix_file_db_mismatch(temp_dirs):
    """Test file-DB sync auto-fix."""
    queue_dir = temp_dirs['queue_dir']
    logs_dir = temp_dirs['logs_dir']
    orchestrator_dir = temp_dirs['orchestrator_dir']
    db_path = temp_dirs['db_path']

    # Create task in DB with queue='claimed'
    task_id = "abc123"
    # Create file first (required for db.create_task)
    task_file_claimed = queue_dir / "claimed" / f"TASK-{task_id}.md"
    task_file_claimed.write_text("# [TASK-abc123] Test\n\nROLE: implement\n")

    with patch('orchestrator.config.get_queue_dir', return_value=queue_dir), \
         patch('orchestrator.db.get_database_path', return_value=db_path):

        db.create_task(
            task_id=task_id,
            file_path=str(task_file_claimed),
            role="implement",
            priority="P1",
            branch="main",
        )

        # Move file to 'incoming' directory (create mismatch)
        task_file_incoming = queue_dir / "incoming" / f"TASK-{task_id}.md"
        os.rename(task_file_claimed, task_file_incoming)

        # Create logger
        logger = QueueManagerLogger(log_dir=logs_dir)

        # Create issue
        issue = {
            "task_id": task_id,
            "db_queue": "claimed",
            "file_queue": "incoming",
            "file_path": str(task_file_incoming),
            "file_mtime": datetime.now().isoformat(),
            "age_seconds": 600,
        }

        # Fix the mismatch
        result = fix_file_db_mismatch(issue, logger)

        # Verify fix was applied
        assert result is True
        task = db.get_task(task_id)
        assert task["queue"] == "incoming"

        # Verify logging
        assert len(logger.actions) == 1
        assert logger.actions[0]["fix_type"] == "file-db-sync"


def test_fix_orphan_file(temp_dirs):
    """Test orphan file registration auto-fix."""
    queue_dir = temp_dirs['queue_dir']
    logs_dir = temp_dirs['logs_dir']
    db_path = temp_dirs['db_path']

    task_id = "orphan1"
    task_file = queue_dir / "incoming" / f"TASK-{task_id}.md"

    content = """# [TASK-orphan1] Orphan Task

ROLE: implement
PRIORITY: P1
BRANCH: main
CREATED: 2026-02-10T12:00:00
CREATED_BY: human

## Context
Test orphan

## Acceptance Criteria
- [ ] Test
"""
    task_file.write_text(content)

    with patch('orchestrator.config.get_queue_dir', return_value=queue_dir), \
         patch('orchestrator.db.get_database_path', return_value=db_path):

        # Create logger
        logger = QueueManagerLogger(log_dir=logs_dir)

        # Create issue
        issue = {
            "task_id": task_id,
            "file_queue": "incoming",
            "file_path": str(task_file),
            "file_mtime": datetime.now().isoformat(),
            "age_seconds": 600,
        }

        # Fix the orphan
        result = fix_orphan_file(issue, logger)

        # Verify fix was applied
        assert result is True
        task = db.get_task(task_id)
        assert task is not None
        assert task["id"] == task_id
        assert task["role"] == "implement"
        assert task["file_path"] == str(task_file)

        # Verify logging
        assert len(logger.actions) == 1
        assert logger.actions[0]["fix_type"] == "orphan-fix"


def test_fix_orphan_file_unparseable(temp_dirs):
    """Test orphan file with unparseable content gets quarantined."""
    queue_dir = temp_dirs['queue_dir']
    logs_dir = temp_dirs['logs_dir']

    task_id = "badfile"
    task_file = queue_dir / "incoming" / f"TASK-{task_id}.md"
    task_file.write_text("Unparseable content\nNo metadata\n")

    quarantine_dir = queue_dir.parent / "quarantine"

    with patch('orchestrator.config.get_queue_dir', return_value=queue_dir):

        logger = QueueManagerLogger(log_dir=logs_dir)

        issue = {
            "task_id": task_id,
            "file_queue": "incoming",
            "file_path": str(task_file),
            "file_mtime": datetime.now().isoformat(),
            "age_seconds": 600,
        }

        result = fix_orphan_file(issue, logger)

        # Verify file was moved to quarantine
        assert result is False
        assert not task_file.exists()
        assert (quarantine_dir / f"TASK-{task_id}.md").exists()

        # Verify escalation was logged
        assert len(logger.actions) == 1
        assert logger.actions[0]["fix_type"] == "escalate"
        assert "quarantine" in logger.actions[0]["message"]


def test_fix_stale_errors(temp_dirs):
    """Test stale error cleanup auto-fix."""
    queue_dir = temp_dirs['queue_dir']
    logs_dir = temp_dirs['logs_dir']
    db_path = temp_dirs['db_path']

    task_id = "retried1"

    # Create file first
    task_file = queue_dir / "incoming" / f"TASK-{task_id}.md"
    content = """# [TASK-retried1] Retried Task

ROLE: implement

## Context
Test

## Acceptance Criteria
- [ ] Test

## FAILED_AT: 2026-02-09T10:00:00

Agent failed with error XYZ.

## Progress Notes
Still here.
"""
    task_file.write_text(content)

    with patch('orchestrator.config.get_queue_dir', return_value=queue_dir), \
         patch('orchestrator.db.get_database_path', return_value=db_path):

        # Create task with attempt_count > 0
        db.create_task(
            task_id=task_id,
            file_path=str(task_file),
            role="implement",
            priority="P1",
            branch="main",
        )

        # Set attempt_count manually
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE tasks SET attempt_count = 1 WHERE id = ?",
                (task_id,)
            )

        # Create logger
        logger = QueueManagerLogger(log_dir=logs_dir)

        # Fix stale errors
        fixed_count = fix_stale_errors(logger)

        # Verify fix was applied
        assert fixed_count == 1
        new_content = task_file.read_text()
        assert "FAILED_AT" not in new_content
        assert "Progress Notes" in new_content  # Other sections preserved

        # Verify logging
        assert len(logger.actions) == 1
        assert logger.actions[0]["fix_type"] == "stale-error"


def test_logger_write_notes_summary(temp_dirs):
    """Test logger writes summary to notes."""
    logs_dir = temp_dirs['logs_dir']
    notes_dir = temp_dirs['notes_dir']

    logger = QueueManagerLogger(log_dir=logs_dir)

    # Log some actions
    logger.log("file-db-sync", "Task abc: synced")
    logger.log("orphan-fix", "Task def: registered")
    logger.log("escalate", "Task ghi: zombie")

    # Write notes summary
    notes_file = logger.write_notes_summary(notes_dir=notes_dir)

    # Verify notes file was created
    assert notes_file.exists()
    content = notes_file.read_text()

    # Verify content
    assert "Queue Manager Auto-Fix Summary" in content
    assert "File-DB syncs: 1" in content
    assert "Orphan files registered: 1" in content
    assert "Issues escalated: 1" in content
    assert "Task abc: synced" in content


def test_logger_log_file_format(temp_dirs):
    """Test logger writes correct format to log file."""
    logs_dir = temp_dirs['logs_dir']

    logger = QueueManagerLogger(log_dir=logs_dir)

    logger.log("file-db-sync", "Test message")

    # Read log file
    log_content = logger.log_file.read_text()

    # Verify format: [timestamp] [fix-type] message
    assert "[file-db-sync]" in log_content
    assert "Test message" in log_content
    # Should have ISO timestamp
    assert "T" in log_content  # ISO format has T between date and time
