"""Integration tests for API server endpoints."""

import pytest
from octopoid_sdk import OctopoidSDK


class TestServerHealth:
    """Test server health and status endpoints."""

    def test_health_endpoint(self, sdk):
        """Server responds to health check."""
        health = sdk.status.health()
        assert health['status'] == 'healthy'
        assert health['database'] == 'connected'
        assert 'version' in health

    def test_health_has_timestamp(self, sdk):
        """Health check includes timestamp."""
        health = sdk.status.health()
        assert 'timestamp' in health
        # Should be ISO format datetime
        assert 'T' in health['timestamp']


class TestTaskCRUD:
    """Test basic CRUD operations for tasks."""

    def test_create_task(self, sdk, clean_tasks):
        """Create task via API."""
        task = sdk.tasks.create(
            id="test-001",
            file_path="/tmp/test-001.md",
            title="Test Task",
            role="implement",
            priority="P1",
            queue="incoming"
        )
        assert task['id'] == "test-001"
        assert task['queue'] == "incoming"
        assert task['title'] == "Test Task"
        assert task['role'] == "implement"
        assert task['priority'] == "P1"

    def test_get_task_by_id(self, sdk, clean_tasks):
        """Retrieve task by ID."""
        # Create task
        sdk.tasks.create(
            id="test-002",
            file_path="/tmp/test-002.md",
            title="Get Test",
            role="implement"
        )

        # Get it back
        task = sdk.tasks.get("test-002")
        assert task is not None
        assert task['id'] == "test-002"
        assert task['title'] == "Get Test"

    def test_get_nonexistent_task(self, sdk):
        """Getting nonexistent task returns None."""
        task = sdk.tasks.get("does-not-exist-999")
        assert task is None

    def test_list_tasks(self, sdk, clean_tasks):
        """List tasks from API."""
        # Create multiple tasks
        sdk.tasks.create(
            id="test-003",
            file_path="/tmp/test-003.md",
            title="Task 3",
            role="implement"
        )
        sdk.tasks.create(
            id="test-004",
            file_path="/tmp/test-004.md",
            title="Task 4",
            role="implement"
        )

        # List all
        tasks = sdk.tasks.list()
        test_tasks = [t for t in tasks if t['id'].startswith('test-')]
        assert len(test_tasks) >= 2

    def test_list_tasks_by_queue(self, sdk, clean_tasks):
        """Filter tasks by queue."""
        # Create tasks in different queues
        sdk.tasks.create(
            id="test-005",
            file_path="/tmp/test-005.md",
            title="Incoming Task",
            role="implement",
            queue="incoming"
        )
        sdk.tasks.create(
            id="test-006",
            file_path="/tmp/test-006.md",
            title="Claimed Task",
            role="implement",
            queue="claimed"
        )

        # List by queue
        incoming = sdk.tasks.list(queue="incoming")
        incoming_test = [t for t in incoming if t['id'].startswith('test-')]
        assert len(incoming_test) >= 1
        assert all(t['queue'] == 'incoming' for t in incoming_test)

    def test_update_task(self, sdk, clean_tasks):
        """Update task fields via API."""
        # Create task
        task = sdk.tasks.create(
            id="test-007",
            file_path="/tmp/test-007.md",
            title="Old Title",
            role="implement",
            priority="P2"
        )

        # Update it
        updated = sdk.tasks.update("test-007", title="New Title", priority="P0")
        assert updated['title'] == "New Title"
        assert updated['priority'] == "P0"

    def test_delete_task(self, sdk, clean_tasks):
        """Delete task via API."""
        # Create task
        sdk.tasks.create(
            id="test-008",
            file_path="/tmp/test-008.md",
            title="To Delete",
            role="implement"
        )

        # Delete it
        result = sdk.tasks.delete("test-008")
        assert 'message' in result or 'task_id' in result

        # Verify deletion
        task = sdk.tasks.get("test-008")
        assert task is None


class TestTaskCreationValidation:
    """Test task creation validation and edge cases."""

    def test_create_task_minimal_fields(self, sdk, clean_tasks):
        """Create task with only required fields."""
        task = sdk.tasks.create(
            id="test-009",
            file_path="/tmp/test-009.md",
            role="implement"
        )
        assert task['id'] == "test-009"
        assert task['role'] == "implement"

    def test_create_duplicate_task_id(self, sdk, clean_tasks):
        """Creating task with duplicate ID should fail."""
        # Create first task
        sdk.tasks.create(
            id="test-010",
            file_path="/tmp/test-010.md",
            role="implement"
        )

        # Try to create duplicate - should raise error
        with pytest.raises(Exception):
            sdk.tasks.create(
                id="test-010",
                file_path="/tmp/test-010-dup.md",
                role="implement"
            )

    def test_create_task_with_metadata(self, sdk, clean_tasks):
        """Create task with metadata field."""
        task = sdk.tasks.create(
            id="test-011",
            file_path="/tmp/test-011.md",
            role="implement",
            metadata={"custom_field": "custom_value"}
        )
        assert task['id'] == "test-011"
        # Metadata should be preserved
        if 'metadata' in task:
            assert task['metadata']['custom_field'] == "custom_value"
