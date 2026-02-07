"""Tests for orchestrator.migrate module."""

import pytest
import argparse
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestMigrateInit:
    """Tests for migrate init command."""

    def test_init_creates_database(self, mock_config, db_path):
        """Test that init creates the database."""
        with patch('orchestrator.migrate.get_database_path', return_value=db_path):
            with patch('orchestrator.db.get_database_path', return_value=db_path):
                with patch('orchestrator.migrate.get_orchestrator_dir', return_value=mock_config):
                    from orchestrator.migrate import cmd_init

                    args = argparse.Namespace(force=False)
                    result = cmd_init(args)

                    assert result == 0
                    assert db_path.exists()

    def test_init_fails_if_db_exists(self, mock_config, db_path):
        """Test that init fails if DB exists without --force."""
        # Create existing DB
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.touch()

        with patch('orchestrator.migrate.get_database_path', return_value=db_path):
            with patch('orchestrator.migrate.get_orchestrator_dir', return_value=mock_config):
                from orchestrator.migrate import cmd_init

                args = argparse.Namespace(force=False)
                result = cmd_init(args)

                assert result == 1

    def test_init_with_force_reinitializes(self, mock_config, db_path):
        """Test that init --force reinitializes existing DB."""
        # Create existing DB with some content
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_text("old content")

        with patch('orchestrator.migrate.get_database_path', return_value=db_path):
            with patch('orchestrator.migrate.get_orchestrator_dir', return_value=mock_config):
                with patch('orchestrator.db.get_database_path', return_value=db_path):
                    from orchestrator.migrate import cmd_init

                    args = argparse.Namespace(force=True)
                    result = cmd_init(args)

                    assert result == 0
                    # DB should be recreated (different content)
                    assert db_path.read_bytes() != b"old content"


class TestMigrateImport:
    """Tests for migrate import command."""

    def test_import_no_database(self, mock_config, db_path):
        """Test import fails if database doesn't exist."""
        with patch('orchestrator.migrate.get_database_path', return_value=db_path):
            from orchestrator.migrate import cmd_import

            args = argparse.Namespace(verbose=False)
            result = cmd_import(args)

            assert result == 1

    def test_import_tasks(self, mock_config, initialized_db, sample_task_file):
        """Test importing existing task files."""
        with patch('orchestrator.migrate.get_database_path', return_value=initialized_db):
            with patch('orchestrator.db.get_database_path', return_value=initialized_db):
                with patch('orchestrator.migrate.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.migrate import cmd_import
                    from orchestrator.db import get_task

                    args = argparse.Namespace(verbose=True)
                    result = cmd_import(args)

                    assert result == 0

                    # Check task was imported
                    task = get_task("abc12345")
                    assert task is not None
                    assert task["priority"] == "P1"

    def test_import_skips_existing(self, mock_config, initialized_db, sample_task_file):
        """Test that import skips already imported tasks."""
        with patch('orchestrator.migrate.get_database_path', return_value=initialized_db):
            with patch('orchestrator.db.get_database_path', return_value=initialized_db):
                with patch('orchestrator.migrate.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.migrate import cmd_import
                    from orchestrator.db import create_task

                    # Pre-create the task in DB
                    create_task(
                        task_id="abc12345",
                        file_path=str(sample_task_file),
                    )

                    args = argparse.Namespace(verbose=True)
                    result = cmd_import(args)

                    # Should succeed but skip the existing task
                    assert result == 0

    def test_import_multiple_queues(self, mock_config, initialized_db):
        """Test importing from multiple queue directories."""
        # Create tasks in different queues
        queue_dir = mock_config / "shared" / "queue"

        (queue_dir / "incoming" / "TASK-inc1.md").write_text(
            "# [TASK-inc1] Incoming\nROLE: implement\nPRIORITY: P1\n"
        )
        (queue_dir / "claimed" / "TASK-claim1.md").write_text(
            "# [TASK-claim1] Claimed\nROLE: implement\nPRIORITY: P2\n"
        )
        (queue_dir / "done" / "TASK-done1.md").write_text(
            "# [TASK-done1] Done\nROLE: implement\nPRIORITY: P1\n"
        )

        with patch('orchestrator.migrate.get_database_path', return_value=initialized_db):
            with patch('orchestrator.db.get_database_path', return_value=initialized_db):
                with patch('orchestrator.migrate.get_queue_dir', return_value=queue_dir):
                    from orchestrator.migrate import cmd_import
                    from orchestrator.db import get_task

                    args = argparse.Namespace(verbose=False)
                    result = cmd_import(args)

                    assert result == 0

                    # Check all tasks were imported with correct queue status
                    inc = get_task("inc1")
                    claim = get_task("claim1")
                    done = get_task("done1")

                    assert inc["queue"] == "incoming"
                    assert claim["queue"] == "claimed"
                    assert done["queue"] == "done"


class TestMigrateStatus:
    """Tests for migrate status command."""

    def test_status_no_database(self, mock_config, db_path):
        """Test status when no database exists."""
        with patch('orchestrator.migrate.get_database_path', return_value=db_path):
            with patch('orchestrator.migrate.get_orchestrator_dir', return_value=mock_config):
                with patch('orchestrator.migrate.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.migrate import cmd_status

                    args = argparse.Namespace()
                    result = cmd_status(args)

                    assert result == 0  # Status always succeeds

    def test_status_with_database(self, mock_config, initialized_db):
        """Test status with initialized database."""
        with patch('orchestrator.migrate.get_database_path', return_value=initialized_db):
            with patch('orchestrator.db.get_database_path', return_value=initialized_db):
                with patch('orchestrator.migrate.get_orchestrator_dir', return_value=mock_config):
                    with patch('orchestrator.migrate.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        from orchestrator.migrate import cmd_status
                        from orchestrator.db import create_task

                        # Add some tasks
                        create_task(task_id="s1", file_path="/s1.md")
                        create_task(task_id="s2", file_path="/s2.md")

                        args = argparse.Namespace()
                        result = cmd_status(args)

                        assert result == 0

    def test_status_shows_blocked_tasks(self, mock_config, initialized_db):
        """Test that status shows blocked tasks."""
        with patch('orchestrator.migrate.get_database_path', return_value=initialized_db):
            with patch('orchestrator.db.get_database_path', return_value=initialized_db):
                with patch('orchestrator.migrate.get_orchestrator_dir', return_value=mock_config):
                    with patch('orchestrator.migrate.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                        from orchestrator.migrate import cmd_status
                        from orchestrator.db import create_task

                        create_task(task_id="blocker", file_path="/blocker.md")
                        create_task(task_id="blocked", file_path="/blocked.md", blocked_by="blocker")

                        args = argparse.Namespace()
                        result = cmd_status(args)

                        assert result == 0


class TestMigrateRollback:
    """Tests for migrate rollback command."""

    def test_rollback_no_database(self, mock_config, db_path):
        """Test rollback when no database exists."""
        with patch('orchestrator.migrate.get_database_path', return_value=db_path):
            from orchestrator.migrate import cmd_rollback

            args = argparse.Namespace(force=True)
            result = cmd_rollback(args)

            assert result == 1

    def test_rollback_requires_force(self, mock_config, initialized_db):
        """Test that rollback requires --force."""
        with patch('orchestrator.migrate.get_database_path', return_value=initialized_db):
            from orchestrator.migrate import cmd_rollback

            args = argparse.Namespace(force=False)
            result = cmd_rollback(args)

            assert result == 1
            assert initialized_db.exists()  # DB not deleted

    def test_rollback_with_force(self, mock_config, initialized_db):
        """Test rollback with --force deletes database."""
        with patch('orchestrator.migrate.get_database_path', return_value=initialized_db):
            from orchestrator.migrate import cmd_rollback

            args = argparse.Namespace(force=True)
            result = cmd_rollback(args)

            assert result == 0
            assert not initialized_db.exists()

    def test_rollback_removes_wal_files(self, mock_config, initialized_db):
        """Test that rollback also removes WAL files."""
        # Create WAL files
        wal_path = initialized_db.with_suffix(".db-wal")
        shm_path = initialized_db.with_suffix(".db-shm")
        wal_path.touch()
        shm_path.touch()

        with patch('orchestrator.migrate.get_database_path', return_value=initialized_db):
            from orchestrator.migrate import cmd_rollback

            args = argparse.Namespace(force=True)
            result = cmd_rollback(args)

            assert result == 0
            assert not initialized_db.exists()
            assert not wal_path.exists()
            assert not shm_path.exists()
