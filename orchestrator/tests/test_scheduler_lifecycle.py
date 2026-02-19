"""Tests for scheduler-owned branch & PR lifecycle.

Tests that:
1. handle_agent_result executes flow steps when outcome is "done" and task is claimed
2. handle_agent_result moves task to failed when outcome is "failed"
3. handle_agent_result handles missing result.json gracefully

Note: Tests for unified handler decisions, orphan sweep, and guard chain are in
tests/test_scheduler_refactor.py (the comprehensive test suite added during the
handler unification refactor).
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_task_dir(tmp_path):
    """Create a minimal task directory structure."""
    task_dir = tmp_path / "TASK-test123"
    task_dir.mkdir()
    worktree = task_dir / "worktree"
    worktree.mkdir()
    return task_dir


@pytest.fixture
def mock_sdk():
    """Create a mock SDK with tasks namespace."""
    sdk = MagicMock()
    sdk.tasks = MagicMock()
    return sdk


@pytest.fixture
def sample_task():
    """A minimal task dict for testing."""
    return {
        "id": "TASK-test123",
        "title": "Test task",
        "role": "implement",
        "queue": "claimed",
        "flow": "default",
    }


# ---------------------------------------------------------------------------
# Test: handle_agent_result executes flow steps on "done"
# ---------------------------------------------------------------------------

class TestHandleAgentResultFlowSteps:
    """Verify handle_agent_result uses flow steps for done outcome."""

    def test_done_outcome_executes_flow_steps(self, tmp_task_dir, mock_sdk, sample_task):
        """When outcome is 'done' and task is claimed, flow steps should execute."""
        # Write result.json with done outcome
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"outcome": "done"}))

        # Mock the SDK to return a claimed task
        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.scheduler.queue_utils") as mock_qu, \
             patch("orchestrator.steps.execute_steps") as mock_execute, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            # Set up a mock flow with claimed -> provisional transition
            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = ["push_branch", "create_pr", "submit_to_server"]
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            # Verify flow was loaded and steps executed
            mock_load_flow.assert_called_once_with("default")
            mock_flow.get_transitions_from.assert_called_once_with("claimed")
            mock_execute.assert_called_once_with(
                ["push_branch", "create_pr", "submit_to_server"],
                sample_task,
                {"outcome": "done"},
                tmp_task_dir,
            )

    def test_done_outcome_fallback_when_no_flow_steps(self, tmp_task_dir, mock_sdk, sample_task):
        """When outcome is 'done' but flow has no steps, fall back to direct submit."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"outcome": "done"}))

        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.scheduler.queue_utils") as mock_qu, \
             patch("orchestrator.steps.execute_steps") as mock_execute, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            # Flow with no runs
            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = []
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            # Should NOT execute steps (no runs defined)
            mock_execute.assert_not_called()
            # Should fall back to direct submit
            mock_sdk.tasks.submit.assert_called_once_with(
                task_id="TASK-test123", commits_count=0, turns_used=0,
            )


# ---------------------------------------------------------------------------
# Test: handle_agent_result moves to failed
# ---------------------------------------------------------------------------

class TestHandleAgentResultFailed:
    """Verify failed outcome transitions task to failed queue."""

    def test_failed_outcome_moves_to_failed(self, tmp_task_dir, mock_sdk, sample_task):
        """When outcome is 'failed', task should move to failed queue."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({
            "outcome": "failed",
            "reason": "Tests don't pass",
        }))

        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.scheduler.queue_utils") as mock_qu:
            mock_qu.get_sdk.return_value = mock_sdk

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            mock_sdk.tasks.update.assert_called_once_with("TASK-test123", queue="failed")

    def test_error_outcome_moves_to_failed(self, tmp_task_dir, mock_sdk, sample_task):
        """When result.json is invalid, task should move to failed queue."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text("not valid json{{{")

        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.scheduler.queue_utils") as mock_qu:
            mock_qu.get_sdk.return_value = mock_sdk

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            mock_sdk.tasks.update.assert_called_once_with("TASK-test123", queue="failed")


# ---------------------------------------------------------------------------
# Test: handle_agent_result with no result.json
# ---------------------------------------------------------------------------

class TestHandleAgentResultNoResult:
    """Verify graceful handling when agent produces no result.json."""

    def test_no_result_json_no_notes_fails(self, tmp_task_dir, mock_sdk, sample_task):
        """No result.json and no notes = failure."""
        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.scheduler.queue_utils") as mock_qu:
            mock_qu.get_sdk.return_value = mock_sdk

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            mock_sdk.tasks.update.assert_called_once_with("TASK-test123", queue="failed")

    def test_no_result_json_with_notes_continues(self, tmp_task_dir, mock_sdk, sample_task):
        """No result.json but has notes = needs_continuation."""
        (tmp_task_dir / "notes.md").write_text("- [12:00:00] Made progress\n")

        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.scheduler.queue_utils") as mock_qu:
            mock_qu.get_sdk.return_value = mock_sdk

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            mock_sdk.tasks.update.assert_called_once_with(
                "TASK-test123", queue="needs_continuation"
            )
