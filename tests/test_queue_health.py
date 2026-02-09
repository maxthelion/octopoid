"""Tests for queue health detection (queue-manager agent)."""

import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the functions we're testing
from orchestrator.scheduler import detect_queue_health_issues, should_trigger_queue_manager


@pytest.fixture
def mock_db_setup(temp_dir):
    """Set up mock database and queue directories."""
    orchestrator_dir = temp_dir / ".orchestrator"
    queue_dir = orchestrator_dir / "shared" / "queue"

    # Create all queue directories
    for queue_name in ["incoming", "claimed", "provisional", "done", "failed",
                       "rejected", "escalated", "recycled", "breakdown", "needs_continuation"]:
        (queue_dir / queue_name).mkdir(parents=True, exist_ok=True)

    agents_dir = orchestrator_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    # Create state.db for DB-enabled mode
    db_path = orchestrator_dir / "state.db"

    # Mock config functions to point to our temp dirs
    with patch('orchestrator.scheduler.find_parent_project', return_value=temp_dir), \
         patch('orchestrator.config.get_orchestrator_dir', return_value=orchestrator_dir), \
         patch('orchestrator.config.get_queue_dir', return_value=queue_dir), \
         patch('orchestrator.config.get_agents_runtime_dir', return_value=agents_dir), \
         patch('orchestrator.config.is_db_enabled', return_value=True):
        yield {
            'temp_dir': temp_dir,
            'queue_dir': queue_dir,
            'agents_dir': agents_dir,
            'orchestrator_dir': orchestrator_dir
        }


def test_detect_no_issues(mock_db_setup):
    """Test detection when there are no issues."""
    with patch('orchestrator.scheduler.db') as mock_db:
        mock_db.list_tasks.return_value = []

        issues = detect_queue_health_issues()

        assert len(issues['file_db_mismatches']) == 0
        assert len(issues['orphan_files']) == 0
        assert len(issues['zombie_claims']) == 0


def test_detect_orphan_file(mock_db_setup):
    """Test detection of orphan file (file exists but not in DB)."""
    queue_dir = mock_db_setup['queue_dir']
    temp_dir = mock_db_setup['temp_dir']

    # Create a task file in incoming queue
    task_file = queue_dir / "incoming" / "TASK-orphan123.md"
    task_file.write_text("# Task orphan123\n\nSome content")

    # Make file old enough (>5 minutes)
    old_time = time.time() - 400  # 6.67 minutes ago
    task_file.touch()
    # Can't easily set mtime in test, so we'll patch stat instead
    original_stat = task_file.stat

    def mock_stat():
        result = original_stat()
        # Create a mock with the old mtime
        mock_result = MagicMock()
        mock_result.st_mtime = old_time
        return mock_result

    with patch('orchestrator.scheduler.db') as mock_db, \
         patch.object(Path, 'stat', mock_stat):
        # DB returns empty list (no tasks)
        mock_db.list_tasks.return_value = []

        issues = detect_queue_health_issues()

        assert len(issues['orphan_files']) == 1
        orphan = issues['orphan_files'][0]
        assert orphan['task_id'] == 'orphan123'
        assert orphan['queue'] == 'incoming'
        assert 'TASK-orphan123.md' in orphan['file_path']


def test_detect_file_db_mismatch(mock_db_setup):
    """Test detection of file-DB mismatch (file in wrong queue)."""
    queue_dir = mock_db_setup['queue_dir']
    temp_dir = mock_db_setup['temp_dir']

    # Create task file in incoming queue
    task_file = queue_dir / "incoming" / "TASK-mismatch456.md"
    task_file.write_text("# Task mismatch456\n\nSome content")

    # Make file old enough
    old_time = time.time() - 400

    def mock_stat():
        mock_result = MagicMock()
        mock_result.st_mtime = old_time
        return mock_result

    with patch('orchestrator.scheduler.db') as mock_db, \
         patch.object(Path, 'stat', mock_stat):
        # DB says task is in 'claimed' queue
        mock_db.list_tasks.return_value = [
            {'id': 'mismatch456', 'queue': 'claimed', 'role': 'implement'}
        ]

        issues = detect_queue_health_issues()

        assert len(issues['file_db_mismatches']) == 1
        mismatch = issues['file_db_mismatches'][0]
        assert mismatch['task_id'] == 'mismatch456'
        assert mismatch['db_queue'] == 'claimed'
        assert mismatch['file_queue'] == 'incoming'


def test_detect_zombie_claim(mock_db_setup):
    """Test detection of zombie claim (old claim, inactive agent)."""
    agents_dir = mock_db_setup['agents_dir']

    # Create agent state file showing old last_finished
    agent_dir = agents_dir / "impl-agent-1"
    agent_dir.mkdir(exist_ok=True)
    state_file = agent_dir / "state.json"

    # Agent last finished 2 hours ago
    last_finished = (datetime.now() - timedelta(hours=2)).isoformat()
    state_file.write_text(f'{{"running": false, "last_finished": "{last_finished}"}}')

    with patch('orchestrator.scheduler.db') as mock_db, \
         patch('orchestrator.scheduler.load_state') as mock_load_state:

        # Task claimed 3 hours ago
        claimed_at = (datetime.now() - timedelta(hours=3)).isoformat()
        mock_db.list_tasks.return_value = [
            {
                'id': 'zombie789',
                'queue': 'claimed',
                'claimed_by': 'impl-agent-1',
                'claimed_at': claimed_at,
                'role': 'implement'
            }
        ]

        # Mock agent state
        mock_state = MagicMock()
        mock_state.last_finished = last_finished
        mock_load_state.return_value = mock_state

        issues = detect_queue_health_issues()

        assert len(issues['zombie_claims']) == 1
        zombie = issues['zombie_claims'][0]
        assert zombie['task_id'] == 'zombie789'
        assert zombie['claimed_by'] == 'impl-agent-1'
        assert zombie['claim_duration_seconds'] > 7200  # More than 2 hours


def test_should_trigger_queue_manager_no_issues(mock_db_setup):
    """Test should_trigger_queue_manager returns False when no issues."""
    with patch('orchestrator.scheduler.detect_queue_health_issues') as mock_detect:
        mock_detect.return_value = {
            'file_db_mismatches': [],
            'orphan_files': [],
            'zombie_claims': []
        }

        should_trigger, reason = should_trigger_queue_manager()

        assert should_trigger is False
        assert reason == ""


def test_should_trigger_queue_manager_with_issues(mock_db_setup):
    """Test should_trigger_queue_manager returns True with issues."""
    with patch('orchestrator.scheduler.detect_queue_health_issues') as mock_detect:
        mock_detect.return_value = {
            'file_db_mismatches': [{'task_id': 'a'}],
            'orphan_files': [{'task_id': 'b'}, {'task_id': 'c'}],
            'zombie_claims': []
        }

        should_trigger, reason = should_trigger_queue_manager()

        assert should_trigger is True
        assert "1 file-DB mismatch(es)" in reason
        assert "2 orphan file(s)" in reason


def test_recent_files_not_detected_as_orphans(mock_db_setup):
    """Test that recently created files are not flagged as orphans."""
    queue_dir = mock_db_setup['queue_dir']

    # Create a task file just now (within 5-minute grace period)
    task_file = queue_dir / "incoming" / "TASK-recent999.md"
    task_file.write_text("# Task recent999\n")

    # File is brand new (current time)
    recent_time = time.time()

    def mock_stat():
        mock_result = MagicMock()
        mock_result.st_mtime = recent_time
        return mock_result

    with patch('orchestrator.scheduler.db') as mock_db, \
         patch.object(Path, 'stat', mock_stat):
        mock_db.list_tasks.return_value = []

        issues = detect_queue_health_issues()

        # Should NOT be detected as orphan due to grace period
        assert len(issues['orphan_files']) == 0
