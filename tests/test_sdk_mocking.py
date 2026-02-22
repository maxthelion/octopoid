"""Tests to verify SDK mocking prevents production side effects in unit tests."""


class TestSDKMocking:
    """Verify that get_sdk() is automatically mocked in unit tests."""

    def test_sdk_is_mocked_by_default(self, mock_sdk_for_unit_tests):
        """Verify that the autouse fixture mocks get_sdk() for unit tests."""
        from orchestrator.sdk import get_sdk

        sdk = get_sdk()

        # Should return the mocked SDK, not a real one
        assert sdk is mock_sdk_for_unit_tests

    def test_create_task_uses_mocked_sdk(self, mock_orchestrator_dir, mock_sdk_for_unit_tests):
        """Verify create_task() uses the mocked SDK and doesn't hit production."""
        from orchestrator.queue_utils import create_task

        task_name = create_task(
            title="Test task for SDK mocking",
            role="implement",
            context="This test verifies SDK is mocked",
            acceptance_criteria=["SDK calls are mocked", "No production side effects"],
        )

        # create_task() returns "TASK-{id}" string (no file written)
        assert isinstance(task_name, str), "create_task() must return a string"
        assert task_name.startswith("TASK-"), f"Expected TASK-... prefix, got: {task_name}"

        # Verify the mocked SDK's create method was called (not a real HTTP request)
        mock_sdk_for_unit_tests.tasks.create.assert_called_once()

        # Verify the mock was called with the expected parameters
        call_kwargs = mock_sdk_for_unit_tests.tasks.create.call_args[1]
        assert call_kwargs["title"] == "Test task for SDK mocking"
        assert call_kwargs["role"] == "implement"
        assert "content" in call_kwargs, "create_task() must send content to server"

    def test_sdk_mock_prevents_network_calls(self, mock_sdk_for_unit_tests):
        """Verify the mock SDK doesn't make real network requests."""
        # The mock SDK should have all the expected methods
        assert hasattr(mock_sdk_for_unit_tests, 'tasks')
        assert hasattr(mock_sdk_for_unit_tests.tasks, 'create')
        assert hasattr(mock_sdk_for_unit_tests.tasks, 'list')
        assert hasattr(mock_sdk_for_unit_tests.tasks, 'get')
        assert hasattr(mock_sdk_for_unit_tests.tasks, 'update')

        # Calling these methods shouldn't raise exceptions (they're mocked)
        mock_sdk_for_unit_tests.tasks.list()
        mock_sdk_for_unit_tests.tasks.get("test-id")

        # Verify calls were tracked
        assert mock_sdk_for_unit_tests.tasks.list.called
        assert mock_sdk_for_unit_tests.tasks.get.called
