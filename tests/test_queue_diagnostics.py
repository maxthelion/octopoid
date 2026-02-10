"""Tests for queue health diagnostics."""

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts directory to path
SCRIPTS_DIR = Path(__file__).parent.parent.parent / ".orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from diagnose_queue_health import (  # noqa: E402
    detect_file_db_mismatches,
    detect_orphan_files,
    detect_zombie_claims,
    find_all_task_files,
    run_diagnostics,
)


@pytest.fixture
def mock_queue_dir(tmp_path):
    """Create a mock queue directory structure."""
    queue_dir = tmp_path / "queue"
    for subdir in ["incoming", "claimed", "provisional", "done", "failed"]:
        (queue_dir / subdir).mkdir(parents=True)
    return queue_dir


@pytest.fixture
def mock_agents_dir(tmp_path):
    """Create a mock agents runtime directory."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)
    return agents_dir


def create_task_file(queue_dir: Path, queue_name: str, task_id: str, age_seconds: float = 0) -> Path:
    """Helper to create a task file with specified age."""
    file_path = queue_dir / queue_name / f"TASK-{task_id}.md"
    file_path.write_text(f"# Task {task_id}\n")

    # Set mtime if age specified
    if age_seconds > 0:
        mtime = (datetime.now() - timedelta(seconds=age_seconds)).timestamp()
        import os
        os.utime(file_path, (mtime, mtime))

    return file_path


def create_agent_state(agents_dir: Path, agent_name: str, last_finished: str | None = None) -> Path:
    """Helper to create an agent state file."""
    agent_dir = agents_dir / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    state_path = agent_dir / "state.json"

    state = {
        "running": False,
        "pid": None,
        "last_finished": last_finished,
    }
    state_path.write_text(json.dumps(state))
    return state_path


class TestFindTaskFiles:
    """Tests for find_all_task_files()."""

    def test_finds_files_in_all_queues(self, mock_queue_dir):
        """Should find task files across all queue directories."""
        with patch("diagnose_queue_health.get_queue_dir", return_value=mock_queue_dir):
            # Create files in different queues
            create_task_file(mock_queue_dir, "incoming", "abc123")
            create_task_file(mock_queue_dir, "claimed", "def456")
            create_task_file(mock_queue_dir, "done", "ghi789")

            files = find_all_task_files()

            assert len(files) == 3
            assert "abc123" in files
            assert "def456" in files
            assert "ghi789" in files
            assert files["abc123"][0] == "incoming"
            assert files["def456"][0] == "claimed"
            assert files["ghi789"][0] == "done"

    def test_empty_queues(self, mock_queue_dir):
        """Should return empty dict when no task files exist."""
        with patch("diagnose_queue_health.get_queue_dir", return_value=mock_queue_dir):
            files = find_all_task_files()
            assert files == {}


class TestDetectFileMismatches:
    """Tests for detect_file_db_mismatches()."""

    def test_detects_mismatch_between_file_and_db(self, mock_queue_dir):
        """Should detect when file location doesn't match DB queue."""
        with patch("diagnose_queue_health.get_queue_dir", return_value=mock_queue_dir):
            # Create file in incoming but DB says claimed
            create_task_file(mock_queue_dir, "incoming", "abc123", age_seconds=600)

            # Mock DB to return claimed
            mock_db_tasks = {"abc123": "claimed"}

            with patch("diagnose_queue_health.db.get_connection") as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.fetchall.return_value = []
                mock_cursor.__iter__.return_value = [
                    {"id": "abc123", "queue": "claimed"}
                ]
                mock_conn.return_value.__enter__.return_value.execute.return_value = mock_cursor

                issues = detect_file_db_mismatches(min_age_seconds=300)

                assert len(issues) == 1
                assert issues[0]["task_id"] == "abc123"
                assert issues[0]["db_queue"] == "claimed"
                assert issues[0]["file_queue"] == "incoming"

    def test_ignores_recent_files(self, mock_queue_dir):
        """Should ignore files younger than min_age_seconds to avoid races."""
        with patch("diagnose_queue_health.get_queue_dir", return_value=mock_queue_dir):
            # Create file with age < threshold
            create_task_file(mock_queue_dir, "incoming", "abc123", age_seconds=60)

            with patch("diagnose_queue_health.db.get_connection") as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.__iter__.return_value = [
                    {"id": "abc123", "queue": "claimed"}
                ]
                mock_conn.return_value.__enter__.return_value.execute.return_value = mock_cursor

                issues = detect_file_db_mismatches(min_age_seconds=300)

                assert len(issues) == 0  # Too recent, ignored

    def test_no_issues_when_file_matches_db(self, mock_queue_dir):
        """Should find no issues when file location matches DB."""
        with patch("diagnose_queue_health.get_queue_dir", return_value=mock_queue_dir):
            create_task_file(mock_queue_dir, "claimed", "abc123", age_seconds=600)

            with patch("diagnose_queue_health.db.get_connection") as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.__iter__.return_value = [
                    {"id": "abc123", "queue": "claimed"}
                ]
                mock_conn.return_value.__enter__.return_value.execute.return_value = mock_cursor

                issues = detect_file_db_mismatches(min_age_seconds=300)

                assert len(issues) == 0


class TestDetectOrphanFiles:
    """Tests for detect_orphan_files()."""

    def test_detects_file_with_no_db_record(self, mock_queue_dir):
        """Should detect task files that don't exist in DB."""
        with patch("diagnose_queue_health.get_queue_dir", return_value=mock_queue_dir):
            # Create file with no DB record
            create_task_file(mock_queue_dir, "incoming", "orphan123", age_seconds=600)

            with patch("diagnose_queue_health.db.get_connection") as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.__iter__.return_value = []  # No tasks in DB
                mock_conn.return_value.__enter__.return_value.execute.return_value = mock_cursor

                issues = detect_orphan_files(min_age_seconds=300)

                assert len(issues) == 1
                assert issues[0]["task_id"] == "orphan123"
                assert issues[0]["file_queue"] == "incoming"

    def test_ignores_recent_orphans(self, mock_queue_dir):
        """Should ignore orphan files younger than min_age_seconds."""
        with patch("diagnose_queue_health.get_queue_dir", return_value=mock_queue_dir):
            create_task_file(mock_queue_dir, "incoming", "orphan123", age_seconds=60)

            with patch("diagnose_queue_health.db.get_connection") as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.__iter__.return_value = []
                mock_conn.return_value.__enter__.return_value.execute.return_value = mock_cursor

                issues = detect_orphan_files(min_age_seconds=300)

                assert len(issues) == 0  # Too recent


class TestDetectZombieClaims:
    """Tests for detect_zombie_claims()."""

    def test_detects_old_claim_with_inactive_agent(self, mock_agents_dir):
        """Should detect claims older than threshold with inactive agent."""
        with patch("diagnose_queue_health.get_agents_runtime_dir", return_value=mock_agents_dir):
            # Create agent state with old last_finished
            old_time = (datetime.now() - timedelta(hours=3)).isoformat()
            create_agent_state(mock_agents_dir, "impl-1", last_finished=old_time)

            # Mock DB to return old claim
            claim_time = (datetime.now() - timedelta(hours=3)).isoformat()

            with patch("diagnose_queue_health.db.get_connection") as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.__iter__.return_value = [
                    {
                        "id": "zombie123",
                        "claimed_by": "impl-1",
                        "claimed_at": claim_time,
                    }
                ]
                mock_conn.return_value.__enter__.return_value.execute.return_value = mock_cursor

                issues = detect_zombie_claims(claim_hours=2, inactive_hours=1)

                assert len(issues) == 1
                assert issues[0]["task_id"] == "zombie123"
                assert issues[0]["claimed_by"] == "impl-1"

    def test_ignores_recent_claims(self, mock_agents_dir):
        """Should ignore claims newer than claim_hours threshold."""
        with patch("diagnose_queue_health.get_agents_runtime_dir", return_value=mock_agents_dir):
            claim_time = (datetime.now() - timedelta(minutes=30)).isoformat()

            with patch("diagnose_queue_health.db.get_connection") as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.__iter__.return_value = [
                    {
                        "id": "task123",
                        "claimed_by": "impl-1",
                        "claimed_at": claim_time,
                    }
                ]
                mock_conn.return_value.__enter__.return_value.execute.return_value = mock_cursor

                issues = detect_zombie_claims(claim_hours=2, inactive_hours=1)

                assert len(issues) == 0  # Too recent

    def test_ignores_claims_with_active_agent(self, mock_agents_dir):
        """Should ignore old claims if agent is still active."""
        with patch("diagnose_queue_health.get_agents_runtime_dir", return_value=mock_agents_dir):
            # Agent was active recently
            recent_time = (datetime.now() - timedelta(minutes=10)).isoformat()
            create_agent_state(mock_agents_dir, "impl-1", last_finished=recent_time)

            # But claim is old
            claim_time = (datetime.now() - timedelta(hours=3)).isoformat()

            with patch("diagnose_queue_health.db.get_connection") as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.__iter__.return_value = [
                    {
                        "id": "task123",
                        "claimed_by": "impl-1",
                        "claimed_at": claim_time,
                    }
                ]
                mock_conn.return_value.__enter__.return_value.execute.return_value = mock_cursor

                issues = detect_zombie_claims(claim_hours=2, inactive_hours=1)

                assert len(issues) == 0  # Agent is active


class TestRunDiagnostics:
    """Tests for run_diagnostics() integration."""

    def test_returns_complete_diagnostic_result(self, mock_queue_dir, mock_agents_dir):
        """Should return diagnostic result with all issue types."""
        with (
            patch("diagnose_queue_health.get_queue_dir", return_value=mock_queue_dir),
            patch("diagnose_queue_health.get_agents_runtime_dir", return_value=mock_agents_dir),
            patch("diagnose_queue_health.db.get_connection") as mock_conn,
        ):
            # Setup mocks for all detectors
            mock_cursor = MagicMock()
            mock_cursor.__iter__.return_value = []
            mock_conn.return_value.__enter__.return_value.execute.return_value = mock_cursor

            result = run_diagnostics()

            assert "timestamp" in result
            assert "file_db_mismatches" in result
            assert "orphan_files" in result
            assert "zombie_claims" in result
            assert isinstance(result["file_db_mismatches"], list)
            assert isinstance(result["orphan_files"], list)
            assert isinstance(result["zombie_claims"], list)


class TestCLI:
    """Tests for CLI interface."""

    def test_json_output(self):
        """Should output valid JSON when --json flag is used."""
        script_path = SCRIPTS_DIR / "diagnose_queue_health.py"

        result = subprocess.run(
            [sys.executable, str(script_path), "--json"],
            capture_output=True,
            text=True,
            cwd=script_path.parent.parent.parent,  # Run from project root
        )

        # Should be valid JSON
        data = json.loads(result.stdout)
        assert "timestamp" in data
        assert "file_db_mismatches" in data
        assert "orphan_files" in data
        assert "zombie_claims" in data

    def test_exit_code_with_issues(self):
        """Should exit 1 when issues are found."""
        # This test uses the real queue to check exit code behavior
        # It's acceptable to check the actual queue state
        script_path = SCRIPTS_DIR / "diagnose_queue_health.py"

        result = subprocess.run(
            [sys.executable, str(script_path), "--json"],
            capture_output=True,
            cwd=script_path.parent.parent.parent,  # Run from project root
        )

        # Parse output to check if issues were found
        try:
            data = json.loads(result.stdout)
            has_issues = (
                len(data.get("file_db_mismatches", [])) > 0 or
                len(data.get("orphan_files", [])) > 0 or
                len(data.get("zombie_claims", [])) > 0
            )
            # Exit code should match whether issues were found
            if has_issues:
                assert result.returncode == 1, "Should exit 1 when issues found"
            else:
                assert result.returncode == 0, "Should exit 0 when no issues found"
        except json.JSONDecodeError:
            pytest.fail(f"Invalid JSON output: {result.stdout[:200]}")
