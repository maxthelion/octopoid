"""Tests for queue manager auto-fixes (Phase 2)."""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.queue_manager_logging import QueueManagerLogger

# diagnose_queue_health lives in .octopoid/scripts/ and may not be installed
try:
    import sys
    SCRIPT_DIR = Path(__file__).parent.parent
    sys.path.insert(0, str(SCRIPT_DIR.parent / ".octopoid" / "scripts"))
    from diagnose_queue_health import fix_orphan_file, parse_task_metadata
    _HAS_DIAG = True
except ImportError:
    _HAS_DIAG = False


@pytest.fixture
def temp_dirs(mock_config):
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
    }


@pytest.mark.skipif(not _HAS_DIAG, reason="diagnose_queue_health script not installed")
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


@pytest.mark.skipif(not _HAS_DIAG, reason="diagnose_queue_health script not installed")
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


# test_fix_file_db_mismatch was removed during database cleanup (used db.create_task, db.get_task)
# test_fix_orphan_file was removed during database cleanup (used db.get_task)
# test_fix_stale_errors was removed during database cleanup (used db.create_task, db.get_connection)


@pytest.mark.skipif(not _HAS_DIAG, reason="diagnose_queue_health script not installed")
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
