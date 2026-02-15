"""Tests to verify SDK mocking prevents production side effects in unit tests."""

from unittest.mock import patch


class TestSDKMocking:
    """Verify that get_sdk() is automatically mocked in unit tests."""

    def test_sdk_is_mocked_by_default(self, mock_sdk_for_unit_tests):
        """Verify that the autouse fixture mocks get_sdk() for unit tests."""
        from orchestrator.queue_utils import get_sdk

        sdk = get_sdk()

        # Should return the mocked SDK, not a real one
        assert sdk is mock_sdk_for_unit_tests

    def test_create_task_uses_mocked_sdk(self, mock_orchestrator_dir, mock_sdk_for_unit_tests):
        """Verify create_task() uses the mocked SDK and doesn't hit production."""
        with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_orchestrator_dir / "runtime" / "shared" / "queue"):
            from orchestrator.queue_utils import create_task

            task_path = create_task(
                title="Test task for SDK mocking",
                role="implement",
                context="This test verifies SDK is mocked",
                acceptance_criteria=["SDK calls are mocked", "No production side effects"],
            )

            # Verify the task file was created locally
            assert task_path.exists()

            # Verify the mocked SDK's create method was called (not a real HTTP request)
            mock_sdk_for_unit_tests.tasks.create.assert_called_once()

            # Verify the mock was called with the expected parameters
            call_kwargs = mock_sdk_for_unit_tests.tasks.create.call_args[1]
            assert call_kwargs["title"] == "Test task for SDK mocking"
            assert call_kwargs["role"] == "implement"

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
