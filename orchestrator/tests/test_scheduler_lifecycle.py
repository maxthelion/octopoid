"""Tests for scheduler-owned branch & PR lifecycle.

Tests that:
1. prepare_task_directory puts worktree on a named branch (not detached HEAD)
2. handle_agent_result executes flow steps when outcome is "done" and task is claimed
3. After steps succeed, the engine calls the right API method based on to_state
4. handle_agent_result moves task to failed when outcome is "failed"
5. handle_agent_result handles missing result.json gracefully
6. PID is only removed on success; kept for retry on failure
7. After 3 consecutive step failures, task is moved to failed
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
# Test: prepare_task_directory creates branch
# ---------------------------------------------------------------------------

class TestPrepareTaskDirectoryCreatesBranch:
    """Verify that after prepare_task_directory, the worktree is on a named branch."""

    @patch("orchestrator.scheduler.get_tasks_dir")
    @patch("orchestrator.scheduler.find_parent_project")
    @patch("orchestrator.scheduler.get_main_branch", return_value="main")
    @patch("orchestrator.scheduler.get_global_instructions_path")
    @patch("orchestrator.scheduler.get_orchestrator_dir")
    def test_prepare_calls_ensure_on_branch(
        self,
        mock_orch_dir,
        mock_gi_path,
        mock_main_branch,
        mock_find_parent,
        mock_get_tasks_dir,
        tmp_path,
    ):
        """After prepare_task_directory, ensure_on_branch is called with the task branch."""
        from orchestrator.scheduler import prepare_task_directory

        # Set up paths
        task_dir = tmp_path / "TASK-abc123"
        task_dir.mkdir()
        worktree_path = task_dir / "worktree"
        worktree_path.mkdir()

        mock_get_tasks_dir.return_value = tmp_path
        mock_find_parent.return_value = tmp_path
        mock_orch_dir.return_value = tmp_path / ".octopoid"

        # Global instructions path
        gi_path = tmp_path / "global_instructions.md"
        gi_path.write_text("")
        mock_gi_path.return_value = gi_path

        # Agent dir with scripts and prompt
        agent_dir = tmp_path / "agent"
        scripts_dir = agent_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "run-tests").write_text("#!/bin/bash\necho test")
        (agent_dir / "prompt.md").write_text("Task: $task_id")

        task = {
            "id": "TASK-abc123",
            "title": "Test task",
            "role": "implement",
        }
        agent_config = {"agent_dir": str(agent_dir)}

        with patch("orchestrator.git_utils.create_task_worktree", return_value=worktree_path), \
             patch("orchestrator.scheduler.RepoManager") as MockRepoManager:
            mock_repo = MagicMock()
            MockRepoManager.return_value = mock_repo

            prepare_task_directory(task, "agent-1", agent_config)

            # Verify RepoManager was created with the worktree path
            MockRepoManager.assert_called_once_with(worktree_path)
            # Verify ensure_on_branch was called with the task branch
            mock_repo.ensure_on_branch.assert_called_once_with("agent/TASK-abc123")


# ---------------------------------------------------------------------------
# Test: handle_agent_result executes flow steps on "done"
# ---------------------------------------------------------------------------

class TestHandleAgentResultFlowSteps:
    """Verify handle_agent_result uses flow steps for done outcome."""

    def test_done_outcome_executes_flow_steps_then_transitions(self, tmp_task_dir, mock_sdk, sample_task):
        """When outcome is 'done' and task is claimed, flow steps should execute then engine transitions."""
        # Write result.json with done outcome
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"outcome": "done"}))

        # Mock the SDK to return a claimed task
        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu, \
             patch("orchestrator.steps.execute_steps") as mock_execute, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            # Set up a mock flow with claimed -> provisional transition
            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = ["push_branch", "create_pr"]
            mock_transition.to_state = "provisional"
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            # Verify flow was loaded and steps executed
            mock_load_flow.assert_called_once_with("default")
            mock_flow.get_transitions_from.assert_called_once_with("claimed")
            mock_execute.assert_called_once_with(
                ["push_branch", "create_pr"],
                sample_task,
                {"outcome": "done"},
                tmp_task_dir,
            )
            # Engine performs the transition after steps
            mock_sdk.tasks.submit.assert_called_once_with(
                task_id="TASK-test123", commits_count=0, turns_used=0
            )

    def test_done_outcome_no_runs_still_transitions(self, tmp_task_dir, mock_sdk, sample_task):
        """When outcome is 'done' and transition has no runs, engine still performs transition."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"outcome": "done"}))

        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu, \
             patch("orchestrator.steps.execute_steps") as mock_execute, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            # Flow with no runs but a to_state of provisional
            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = []
            mock_transition.to_state = "provisional"
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            # No steps executed
            mock_execute.assert_not_called()
            # Engine still performs the transition via submit
            mock_sdk.tasks.submit.assert_called_once_with(
                task_id="TASK-test123", commits_count=0, turns_used=0,
            )

    def test_done_outcome_fallback_direct_submit_when_no_transitions(self, tmp_task_dir, mock_sdk, sample_task):
        """When flow has no transition from claimed at all, fall back to direct submit."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"outcome": "done"}))

        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu, \
             patch("orchestrator.steps.execute_steps") as mock_execute, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            # Flow with no transitions from claimed
            mock_flow = MagicMock()
            mock_flow.get_transitions_from.return_value = []
            mock_load_flow.return_value = mock_flow

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            # Should NOT execute steps
            mock_execute.assert_not_called()
            # Should fall back to direct submit
            mock_sdk.tasks.submit.assert_called_once_with(
                task_id="TASK-test123", commits_count=0, turns_used=0,
            )

    def test_child_flow_done_outcome_accepts_task(self, tmp_task_dir, mock_sdk):
        """Child flow 'claimed -> done' transition causes engine to call sdk.tasks.accept()."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"outcome": "done"}))

        project_task = {
            "id": "TASK-test123",
            "title": "Child task",
            "queue": "claimed",
            "flow": "project",
            "project_id": "PROJ-abc",
        }
        mock_sdk.tasks.get.return_value = project_task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu, \
             patch("orchestrator.steps.execute_steps") as mock_execute, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            child_transition = MagicMock()
            child_transition.runs = ["rebase_on_project_branch", "run_tests"]
            child_transition.to_state = "done"

            mock_child_flow = MagicMock()
            mock_child_flow.get_transitions_from.return_value = [child_transition]

            mock_flow = MagicMock()
            mock_flow.child_flow = mock_child_flow
            mock_flow.get_transitions_from.return_value = []
            mock_load_flow.return_value = mock_flow

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            # Steps executed
            mock_execute.assert_called_once_with(
                ["rebase_on_project_branch", "run_tests"],
                project_task,
                {"outcome": "done"},
                tmp_task_dir,
            )
            # Engine accepts (not submits) for claimed -> done
            mock_sdk.tasks.accept.assert_called_once_with(
                task_id="TASK-test123", accepted_by="flow-engine"
            )
            mock_sdk.tasks.submit.assert_not_called()


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

        with patch("orchestrator.result_handler.queue_utils") as mock_qu:
            mock_qu.get_sdk.return_value = mock_sdk

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            mock_sdk.tasks.update.assert_called_once_with("TASK-test123", queue="failed")

    def test_error_outcome_moves_to_failed(self, tmp_task_dir, mock_sdk, sample_task):
        """When result.json is invalid, task should move to failed queue."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text("not valid json{{{")

        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu:
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

        with patch("orchestrator.result_handler.queue_utils") as mock_qu:
            mock_qu.get_sdk.return_value = mock_sdk

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            mock_sdk.tasks.update.assert_called_once_with("TASK-test123", queue="failed")

    def test_no_result_json_with_notes_continues(self, tmp_task_dir, mock_sdk, sample_task):
        """No result.json but has notes = needs_continuation."""
        (tmp_task_dir / "notes.md").write_text("- [12:00:00] Made progress\n")

        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu:
            mock_qu.get_sdk.return_value = mock_sdk

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            mock_sdk.tasks.update.assert_called_once_with(
                "TASK-test123", queue="needs_continuation"
            )

    def test_done_outcome_non_claimed_queue_skips(self, tmp_task_dir, mock_sdk):
        """Done outcome when task is already provisional should not re-execute steps."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"outcome": "done"}))

        task = {
            "id": "TASK-test123",
            "title": "Test task",
            "queue": "provisional",
            "flow": "default",
        }
        mock_sdk.tasks.get.return_value = task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu, \
             patch("orchestrator.steps.execute_steps") as mock_execute, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            # Should NOT execute steps or submit — task already moved past claimed
            mock_execute.assert_not_called()
            mock_sdk.tasks.submit.assert_not_called()


# ---------------------------------------------------------------------------
# Test: child_flow dispatch for project tasks
# ---------------------------------------------------------------------------

class TestChildFlowDispatch:
    """Verify child_flow transitions are used for tasks with project_id."""

    def test_project_task_uses_child_flow_in_handle_done(self, tmp_task_dir, mock_sdk):
        """When a task has project_id and the flow has child_flow, use child_flow transitions."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"outcome": "done"}))

        # Task is a child of a project
        project_task = {
            "id": "TASK-test123",
            "title": "Child task",
            "role": "implement",
            "queue": "claimed",
            "flow": "project",
            "project_id": "PROJ-abc",
        }
        mock_sdk.tasks.get.return_value = project_task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu, \
             patch("orchestrator.steps.execute_steps") as mock_execute, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            # Set up a parent flow with child_flow
            child_transition = MagicMock()
            child_transition.runs = ["rebase_on_project_branch", "run_tests"]
            child_transition.to_state = "done"

            mock_child_flow = MagicMock()
            mock_child_flow.get_transitions_from.return_value = [child_transition]

            mock_flow = MagicMock()
            mock_flow.child_flow = mock_child_flow
            # Parent flow has no transitions from "claimed"
            mock_flow.get_transitions_from.return_value = []
            mock_load_flow.return_value = mock_flow

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            # child_flow transitions should be used, not parent flow
            mock_child_flow.get_transitions_from.assert_called_once_with("claimed")
            mock_flow.get_transitions_from.assert_not_called()
            mock_execute.assert_called_once_with(
                ["rebase_on_project_branch", "run_tests"],
                project_task,
                {"outcome": "done"},
                tmp_task_dir,
            )
            # Engine accepts for claimed -> done
            mock_sdk.tasks.accept.assert_called_once_with(
                task_id="TASK-test123", accepted_by="flow-engine"
            )

    def test_non_project_task_uses_normal_flow_in_handle_done(self, tmp_task_dir, mock_sdk, sample_task):
        """When a task has no project_id, use normal top-level flow transitions."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"outcome": "done"}))

        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu, \
             patch("orchestrator.steps.execute_steps") as mock_execute, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            mock_transition = MagicMock()
            mock_transition.runs = ["push_branch", "create_pr"]
            mock_transition.to_state = "provisional"

            mock_child_flow = MagicMock()

            mock_flow = MagicMock()
            mock_flow.child_flow = mock_child_flow
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from orchestrator.scheduler import handle_agent_result
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

            # Top-level flow transitions should be used
            mock_flow.get_transitions_from.assert_called_once_with("claimed")
            mock_child_flow.get_transitions_from.assert_not_called()
            mock_execute.assert_called_once_with(
                ["push_branch", "create_pr"],
                sample_task,
                {"outcome": "done"},
                tmp_task_dir,
            )
            # Engine submits for claimed -> provisional
            mock_sdk.tasks.submit.assert_called_once_with(
                task_id="TASK-test123", commits_count=0, turns_used=0
            )

    def test_project_task_uses_child_flow_in_flow_dispatch(self, tmp_task_dir, mock_sdk):
        """handle_agent_result_via_flow uses child_flow transitions for project tasks."""
        # Write a "approve" result (gatekeeper-style)
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"status": "success", "decision": "approve"}))

        project_task = {
            "id": "TASK-test123",
            "title": "Child task",
            "role": "implement",
            "queue": "claimed",
            "flow": "project",
            "project_id": "PROJ-abc",
        }
        mock_sdk.tasks.get.return_value = project_task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu, \
             patch("orchestrator.steps.execute_steps") as mock_execute, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            child_transition = MagicMock()
            child_transition.conditions = []
            child_transition.runs = ["rebase_on_project_branch", "run_tests"]

            mock_child_flow = MagicMock()
            mock_child_flow.get_transitions_from.return_value = [child_transition]

            mock_flow = MagicMock()
            mock_flow.child_flow = mock_child_flow
            mock_flow.get_transitions_from.return_value = []
            mock_load_flow.return_value = mock_flow

            from orchestrator.scheduler import handle_agent_result_via_flow
            handle_agent_result_via_flow("TASK-test123", "agent-1", tmp_task_dir)

            mock_child_flow.get_transitions_from.assert_called_once_with("claimed")
            mock_flow.get_transitions_from.assert_not_called()
            mock_execute.assert_called_once()

    def test_non_project_task_uses_normal_flow_in_flow_dispatch(self, tmp_task_dir, mock_sdk, sample_task):
        """handle_agent_result_via_flow uses top-level flow for tasks without project_id."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"status": "success", "decision": "approve"}))

        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu, \
             patch("orchestrator.steps.execute_steps") as mock_execute, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            mock_transition = MagicMock()
            mock_transition.conditions = []
            mock_transition.runs = ["post_review_comment", "merge_pr"]
            mock_transition.to_state = "done"

            mock_child_flow = MagicMock()

            mock_flow = MagicMock()
            mock_flow.child_flow = mock_child_flow
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from orchestrator.scheduler import handle_agent_result_via_flow
            handle_agent_result_via_flow("TASK-test123", "agent-1", tmp_task_dir)

            mock_flow.get_transitions_from.assert_called_once_with("claimed")
            mock_child_flow.get_transitions_from.assert_not_called()
            mock_execute.assert_called_once()


# ---------------------------------------------------------------------------
# Test: step failure retry counter and PID cleanup
# ---------------------------------------------------------------------------

class TestStepFailureRetry:
    """Verify step failure counting and PID-retention behavior."""

    def test_step_failure_raises_so_pid_is_retained(self, tmp_task_dir, mock_sdk, sample_task):
        """When a step raises, handle_agent_result re-raises so caller keeps the PID."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"outcome": "done"}))

        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = ["push_branch", "create_pr"]
            mock_transition.to_state = "provisional"
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            # Make execute_steps raise
            with patch("orchestrator.steps.execute_steps", side_effect=RuntimeError("Tests failed")):
                from orchestrator.scheduler import handle_agent_result
                with pytest.raises(RuntimeError, match="Tests failed"):
                    handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

        # Failure count file should have been incremented
        counter_file = tmp_task_dir / "step_failure_count"
        assert counter_file.exists()
        assert counter_file.read_text().strip() == "1"

    def test_after_3_failures_moves_to_failed_and_returns_cleanly(self, tmp_task_dir, mock_sdk, sample_task):
        """After 3 consecutive step failures, handle_agent_result moves to failed and returns (no raise)."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"outcome": "done"}))

        # Pre-seed the counter to 2 (this will be the 3rd failure)
        (tmp_task_dir / "step_failure_count").write_text("2")

        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = ["push_branch"]
            mock_transition.to_state = "provisional"
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            with patch("orchestrator.steps.execute_steps", side_effect=RuntimeError("Step failed again")):
                from orchestrator.scheduler import handle_agent_result
                # Should NOT raise — returns cleanly so PID gets removed
                handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

        # Task moved to failed
        mock_sdk.tasks.update.assert_called_once()
        call_args = mock_sdk.tasks.update.call_args
        assert call_args[0][0] == "TASK-test123"
        assert call_args[1]["queue"] == "failed"

        # Counter reset
        counter_file = tmp_task_dir / "step_failure_count"
        assert not counter_file.exists()

    def test_success_resets_failure_counter(self, tmp_task_dir, mock_sdk, sample_task):
        """After a successful run, the failure counter file is no longer consulted (implicit reset)."""
        result_path = tmp_task_dir / "result.json"
        result_path.write_text(json.dumps({"outcome": "done"}))

        mock_sdk.tasks.get.return_value = sample_task

        with patch("orchestrator.result_handler.queue_utils") as mock_qu, \
             patch("orchestrator.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = []
            mock_transition.to_state = "provisional"
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from orchestrator.scheduler import handle_agent_result
            # Should not raise
            handle_agent_result("TASK-test123", "agent-1", tmp_task_dir)

        # submit called (transition worked)
        mock_sdk.tasks.submit.assert_called_once_with(
            task_id="TASK-test123", commits_count=0, turns_used=0
        )


# ---------------------------------------------------------------------------
# Test: guard_task_description_nonempty
# ---------------------------------------------------------------------------

class TestGuardTaskDescriptionNonempty:
    """Verify guard_task_description_nonempty blocks spawning for empty tasks."""

    def _make_ctx(self, claimed_task: dict | None, spawn_mode: str = "scripts") -> object:
        from orchestrator.scheduler import AgentContext
        from orchestrator.state_utils import AgentState
        state = AgentState()
        return AgentContext(
            agent_config={"spawn_mode": spawn_mode},
            agent_name="implementer-1",
            role="implement",
            interval=60,
            state=state,
            state_path=Path("/tmp/state.json"),
            claimed_task=claimed_task,
        )

    def test_no_claimed_task_passes(self):
        """Guard passes when no task is claimed."""
        from orchestrator.scheduler import guard_task_description_nonempty
        ctx = self._make_ctx(None)
        proceed, reason = guard_task_description_nonempty(ctx)
        assert proceed is True
        assert reason == ""

    def test_non_scripts_mode_passes(self):
        """Guard passes for non-scripts-mode agents (they claim their own tasks)."""
        from orchestrator.scheduler import guard_task_description_nonempty
        ctx = self._make_ctx({"id": "abc123", "content": ""}, spawn_mode="worktree")
        proceed, reason = guard_task_description_nonempty(ctx)
        assert proceed is True
        assert reason == ""

    def test_task_with_nonempty_content_passes(self):
        """Guard passes when task content is present and non-empty."""
        from orchestrator.scheduler import guard_task_description_nonempty
        ctx = self._make_ctx({
            "id": "abc123",
            "content": "# Task\n\nDo something useful.\n",
        })
        proceed, reason = guard_task_description_nonempty(ctx)
        assert proceed is True
        assert reason == ""

    def test_task_with_whitespace_only_content_fails(self, tmp_path):
        """Guard blocks spawn and fails task when content is only whitespace."""
        from orchestrator.scheduler import guard_task_description_nonempty

        mock_sdk = MagicMock()
        ctx = self._make_ctx({"id": "abc123", "content": "   \n   "})

        with patch("orchestrator.scheduler.queue_utils") as mock_qu, \
             patch("orchestrator.scheduler.get_tasks_file_dir", return_value=tmp_path):
            mock_qu.get_sdk.return_value = mock_sdk
            proceed, reason = guard_task_description_nonempty(ctx)

        assert proceed is False
        assert "empty_description" in reason
        mock_sdk.tasks.update.assert_called_once_with("abc123", queue="failed", claimed_by=None)

    def test_task_with_no_content_field_fails(self, tmp_path):
        """Guard blocks spawn when task has no content field at all."""
        from orchestrator.scheduler import guard_task_description_nonempty

        mock_sdk = MagicMock()
        ctx = self._make_ctx({"id": "abc123"})

        with patch("orchestrator.scheduler.queue_utils") as mock_qu, \
             patch("orchestrator.scheduler.get_tasks_file_dir", return_value=tmp_path):
            mock_qu.get_sdk.return_value = mock_sdk
            proceed, reason = guard_task_description_nonempty(ctx)

        assert proceed is False
        assert "empty_description" in reason
        assert "abc123" in reason
        mock_sdk.tasks.update.assert_called_once_with("abc123", queue="failed", claimed_by=None)

    def test_empty_content_reason_mentions_file_path_when_missing(self, tmp_path):
        """Error reason mentions expected file path when file is missing."""
        from orchestrator.scheduler import guard_task_description_nonempty

        mock_sdk = MagicMock()
        ctx = self._make_ctx({"id": "abc123", "content": ""})

        with patch("orchestrator.scheduler.queue_utils") as mock_qu, \
             patch("orchestrator.scheduler.get_tasks_file_dir", return_value=tmp_path):
            mock_qu.get_sdk.return_value = mock_sdk
            proceed, reason = guard_task_description_nonempty(ctx)

        assert proceed is False
        assert "TASK-abc123.md" in reason

    def test_empty_content_with_existing_empty_file_mentions_file(self, tmp_path):
        """Error reason mentions the file path when file exists but is empty."""
        from orchestrator.scheduler import guard_task_description_nonempty

        mock_sdk = MagicMock()
        task_file = tmp_path / "TASK-abc123.md"
        task_file.write_text("")

        ctx = self._make_ctx({
            "id": "abc123",
            "content": "",
            "file_path": str(task_file),
        })

        with patch("orchestrator.scheduler.queue_utils") as mock_qu, \
             patch("orchestrator.scheduler.get_tasks_file_dir", return_value=tmp_path):
            mock_qu.get_sdk.return_value = mock_sdk
            proceed, reason = guard_task_description_nonempty(ctx)

        assert proceed is False
        assert "exists but has no content" in reason
        mock_sdk.tasks.update.assert_called_once_with("abc123", queue="failed", claimed_by=None)

    def test_sdk_failure_still_blocks_spawn(self, tmp_path):
        """Guard still blocks spawn even if the SDK call to fail the task throws."""
        from orchestrator.scheduler import guard_task_description_nonempty

        mock_sdk = MagicMock()
        mock_sdk.tasks.update.side_effect = RuntimeError("network error")
        ctx = self._make_ctx({"id": "abc123", "content": ""})

        with patch("orchestrator.scheduler.queue_utils") as mock_qu, \
             patch("orchestrator.scheduler.get_tasks_file_dir", return_value=tmp_path):
            mock_qu.get_sdk.return_value = mock_sdk
            proceed, reason = guard_task_description_nonempty(ctx)

        # Guard still blocks even though SDK threw
        assert proceed is False
        assert "empty_description" in reason
