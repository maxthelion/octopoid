"""Tests for draft operations in orchestrator.db module."""

import pytest
from unittest.mock import patch
from datetime import datetime


class TestDraftSchema:
    """Tests for drafts table schema."""

    def test_init_schema_creates_drafts_table(self, mock_config, db_path):
        """Test that init_schema creates the drafts table."""
        with patch('orchestrator.db.get_database_path', return_value=db_path):
            from orchestrator.db import init_schema, get_connection

            init_schema()

            with get_connection() as conn:
                # Check drafts table exists
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='drafts'"
                )
                assert cursor.fetchone() is not None

                # Check indexes exist
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_drafts_status'"
                )
                assert cursor.fetchone() is not None

                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_drafts_author'"
                )
                assert cursor.fetchone() is not None

                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_drafts_domain'"
                )
                assert cursor.fetchone() is not None

    def test_migration_from_v10_adds_drafts_table(self, mock_config, db_path):
        """Test that migration from v10 to v11 creates drafts table."""
        with patch('orchestrator.db.get_database_path', return_value=db_path):
            from orchestrator.db import get_connection, init_schema, SCHEMA_VERSION

            # Create v10 schema manually
            with get_connection() as conn:
                # Create schema_info table with v10
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS schema_info (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_info (key, value) VALUES (?, ?)",
                    ("version", "10"),
                )

            # Reset the _schema_checked flag to force migration check
            import orchestrator.db as db_mod
            db_mod._schema_checked = False

            # Now trigger migration by getting a connection
            with get_connection() as conn:
                # Check that migration ran
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='drafts'"
                )
                assert cursor.fetchone() is not None

                # Check version was updated
                cursor = conn.execute(
                    "SELECT value FROM schema_info WHERE key = 'version'"
                )
                row = cursor.fetchone()
                assert row is not None
                assert int(row['value']) == SCHEMA_VERSION


class TestDraftOperations:
    """Tests for draft CRUD operations."""

    def test_create_draft(self, initialized_db):
        """Test creating a draft."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_draft, get_draft

            draft = create_draft(
                draft_id="DRAFT-test",
                title="Test Draft",
                author="human",
                file_path="project-management/drafts/boxen/test.md",
                status="idea",
                domain="boxen",
            )

            assert draft["id"] == "DRAFT-test"
            assert draft["title"] == "Test Draft"
            assert draft["author"] == "human"
            assert draft["status"] == "idea"
            assert draft["domain"] == "boxen"
            assert draft["file_path"] == "project-management/drafts/boxen/test.md"
            assert draft["created_at"] is not None
            assert draft["updated_at"] is not None

    def test_get_draft(self, initialized_db):
        """Test getting a draft by ID."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_draft, get_draft

            create_draft(
                draft_id="DRAFT-get",
                title="Get Test",
                author="human",
                file_path="test.md",
            )

            draft = get_draft("DRAFT-get")
            assert draft is not None
            assert draft["id"] == "DRAFT-get"
            assert draft["title"] == "Get Test"

    def test_get_draft_not_found(self, initialized_db):
        """Test getting a non-existent draft returns None."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import get_draft

            draft = get_draft("DRAFT-nonexistent")
            assert draft is None

    def test_list_drafts_no_filters(self, initialized_db):
        """Test listing all drafts without filters."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_draft, list_drafts

            create_draft("DRAFT-1", "First", "human", "test1.md", status="idea", domain="boxen")
            create_draft("DRAFT-2", "Second", "agent-1", "test2.md", status="proposed", domain="octopoid")
            create_draft("DRAFT-3", "Third", "human", "test3.md", status="discussion", domain="boxen")

            drafts = list_drafts()
            assert len(drafts) == 3
            # Should be sorted by created_at descending
            assert drafts[0]["id"] == "DRAFT-3"
            assert drafts[1]["id"] == "DRAFT-2"
            assert drafts[2]["id"] == "DRAFT-1"

    def test_list_drafts_filter_by_status(self, initialized_db):
        """Test listing drafts filtered by status."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_draft, list_drafts

            create_draft("DRAFT-1", "First", "human", "test1.md", status="idea")
            create_draft("DRAFT-2", "Second", "human", "test2.md", status="proposed")
            create_draft("DRAFT-3", "Third", "human", "test3.md", status="idea")

            drafts = list_drafts(status="idea")
            assert len(drafts) == 2
            assert all(d["status"] == "idea" for d in drafts)

    def test_list_drafts_filter_by_author(self, initialized_db):
        """Test listing drafts filtered by author."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_draft, list_drafts

            create_draft("DRAFT-1", "First", "human", "test1.md")
            create_draft("DRAFT-2", "Second", "agent-1", "test2.md")
            create_draft("DRAFT-3", "Third", "agent-1", "test3.md")

            drafts = list_drafts(author="agent-1")
            assert len(drafts) == 2
            assert all(d["author"] == "agent-1" for d in drafts)

    def test_list_drafts_filter_by_domain(self, initialized_db):
        """Test listing drafts filtered by domain."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_draft, list_drafts

            create_draft("DRAFT-1", "First", "human", "test1.md", domain="boxen")
            create_draft("DRAFT-2", "Second", "human", "test2.md", domain="octopoid")
            create_draft("DRAFT-3", "Third", "human", "test3.md", domain="boxen")

            drafts = list_drafts(domain="boxen")
            assert len(drafts) == 2
            assert all(d["domain"] == "boxen" for d in drafts)

    def test_list_drafts_multiple_filters(self, initialized_db):
        """Test listing drafts with multiple filters."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_draft, list_drafts

            create_draft("DRAFT-1", "First", "human", "test1.md", status="idea", domain="boxen")
            create_draft("DRAFT-2", "Second", "human", "test2.md", status="proposed", domain="boxen")
            create_draft("DRAFT-3", "Third", "agent-1", "test3.md", status="idea", domain="boxen")
            create_draft("DRAFT-4", "Fourth", "human", "test4.md", status="idea", domain="octopoid")

            drafts = list_drafts(status="idea", domain="boxen")
            assert len(drafts) == 2
            assert all(d["status"] == "idea" and d["domain"] == "boxen" for d in drafts)

    def test_update_draft(self, initialized_db):
        """Test updating draft fields."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_draft, update_draft, get_draft

            draft = create_draft("DRAFT-update", "Original", "human", "test.md", status="idea")
            original_updated_at = draft["updated_at"]

            # Update some fields
            updated = update_draft("DRAFT-update", title="Updated Title", tags="test,draft")

            assert updated["id"] == "DRAFT-update"
            assert updated["title"] == "Updated Title"
            assert updated["tags"] == "test,draft"
            assert updated["status"] == "idea"  # Unchanged
            assert updated["updated_at"] > original_updated_at

    def test_update_draft_status(self, initialized_db):
        """Test updating draft status specifically."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_draft, update_draft_status, get_draft

            create_draft("DRAFT-status", "Test", "human", "test.md", status="idea")

            updated = update_draft_status("DRAFT-status", "proposed")

            assert updated["status"] == "proposed"
            assert updated["title"] == "Test"  # Unchanged

    def test_update_draft_not_found(self, initialized_db):
        """Test updating non-existent draft returns None."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import update_draft

            result = update_draft("DRAFT-nonexistent", title="Updated")
            assert result is None

    def test_delete_draft(self, initialized_db):
        """Test deleting a draft."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_draft, delete_draft, get_draft

            create_draft("DRAFT-delete", "Test", "human", "test.md")

            result = delete_draft("DRAFT-delete")
            assert result is True

            # Verify it's gone
            draft = get_draft("DRAFT-delete")
            assert draft is None

    def test_delete_draft_not_found(self, initialized_db):
        """Test deleting non-existent draft returns False."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import delete_draft

            result = delete_draft("DRAFT-nonexistent")
            assert result is False

    def test_create_draft_with_links(self, initialized_db):
        """Test creating a draft with linked task and project."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_draft, get_draft

            draft = create_draft(
                draft_id="DRAFT-linked",
                title="Linked Draft",
                author="human",
                file_path="test.md",
                linked_task_id="TASK-123",
                linked_project_id="PROJ-456",
            )

            assert draft["linked_task_id"] == "TASK-123"
            assert draft["linked_project_id"] == "PROJ-456"

    def test_draft_default_status(self, initialized_db):
        """Test that drafts default to 'idea' status."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            from orchestrator.db import create_draft, get_draft

            # Create without specifying status
            draft = create_draft(
                draft_id="DRAFT-default",
                title="Default Status",
                author="human",
                file_path="test.md",
            )

            assert draft["status"] == "idea"
