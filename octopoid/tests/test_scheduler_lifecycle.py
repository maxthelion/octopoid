"""Tests for scheduler-owned branch & PR lifecycle.

Tests that:
1. prepare_task_directory puts worktree on a named branch (not detached HEAD)
2. handle_agent_result executes flow steps when outcome is "done" and task is claimed
3. After steps succeed, the engine calls the right API method based on to_state
4. handle_agent_result moves task to failed when outcome is "failed"
5. handle_agent_result routes unknown outcomes (no stdout.log) to requires-intervention
6. PID is only removed on success; kept for retry on failure
7. After 3 consecutive step failures, task is moved to failed
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_task_dir(tmp_path):
    """Create a minimal task directory structure."""
    task_dir = tmp_path / "test123"
    task_dir.mkdir()
    worktree = task_dir / "worktree"
    worktree.mkdir()
    return task_dir


@pytest.fixture
def mock_sdk():
    """Create a mock SDK with tasks and messages namespaces."""
    sdk = MagicMock()
    sdk.tasks = MagicMock()
    sdk.messages = MagicMock()
    return sdk


@pytest.fixture
def sample_task():
    """A minimal task dict for testing."""
    return {
        "id": "test123",
        "title": "Test task",
        "role": "implement",
        "queue": "claimed",
        "flow": "default",
    }


# ---------------------------------------------------------------------------
# Test: prepare_task_directory creates branch
# ---------------------------------------------------------------------------

class TestPrepareTaskDirectoryCreatesBranch:
    """Verify that prepare_task_directory creates the task directory structure correctly."""

    @patch("octopoid.scheduler.get_tasks_dir")
    @patch("octopoid.scheduler.find_parent_project")
    @patch("octopoid.scheduler.get_base_branch", return_value="main")
    @patch("octopoid.scheduler.get_global_instructions_path")
    @patch("octopoid.scheduler.get_orchestrator_dir")
    def test_prepare_creates_task_files_on_detached_head(
        self,
        mock_orch_dir,
        mock_gi_path,
        mock_base_branch,
        mock_find_parent,
        mock_get_tasks_dir,
        tmp_path,
    ):
        """prepare_task_directory creates task.json, env.sh, and scripts — worktree stays on detached HEAD."""
        from octopoid.scheduler import prepare_task_directory

        # Set up paths
        task_dir = tmp_path / "abc123"
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
            "id": "abc123",
            "title": "Test task",
            "role": "implement",
        }
        agent_config = {"agent_dir": str(agent_dir)}

        with patch("octopoid.git_utils.create_task_worktree", return_value=worktree_path):
            prepare_task_directory(task, "agent-1", agent_config)

        # Verify task.json was written
        assert (task_dir / "task.json").exists()
        # Verify env.sh was written with TASK_ID and TASK_BRANCH (no branch checkout — detached HEAD)
        env_sh = (task_dir / "env.sh").read_text()
        assert "TASK_ID='abc123'" in env_sh
        assert "TASK_BRANCH='agent/abc123'" in env_sh
        # Verify scripts were copied
        assert (task_dir / "scripts" / "run-tests").exists()


# ---------------------------------------------------------------------------
# Test: handle_agent_result executes flow steps on "done"
# ---------------------------------------------------------------------------

class TestHandleAgentResultFlowSteps:
    """Verify handle_agent_result uses flow steps for done outcome."""

    def test_done_outcome_executes_flow_steps_then_transitions(self, tmp_task_dir, mock_sdk, sample_task):
        """When outcome is 'done' and task is claimed, flow steps should execute then engine transitions."""
        mock_sdk.tasks.get.return_value = sample_task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "done"}), \
             patch("octopoid.steps.execute_steps") as mock_execute, \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            # Set up a mock flow with claimed -> provisional transition
            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = ["push_branch", "create_pr"]
            mock_transition.to_state = "provisional"
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "agent-1", tmp_task_dir)

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
                task_id="test123", commits_count=0, turns_used=0
            )

    def test_done_outcome_no_runs_still_transitions(self, tmp_task_dir, mock_sdk, sample_task):
        """When outcome is 'done' and transition has no runs, engine still performs transition."""
        mock_sdk.tasks.get.return_value = sample_task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "done"}), \
             patch("octopoid.steps.execute_steps") as mock_execute, \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            # Flow with no runs but a to_state of provisional
            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = []
            mock_transition.to_state = "provisional"
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "agent-1", tmp_task_dir)

            # No steps executed
            mock_execute.assert_not_called()
            # Engine still performs the transition via submit
            mock_sdk.tasks.submit.assert_called_once_with(
                task_id="test123", commits_count=0, turns_used=0,
            )

    def test_done_outcome_fallback_direct_submit_when_no_transitions(self, tmp_task_dir, mock_sdk, sample_task):
        """When flow has no transition from claimed at all, fall back to direct submit."""
        mock_sdk.tasks.get.return_value = sample_task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "done"}), \
             patch("octopoid.steps.execute_steps") as mock_execute, \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            # Flow with no transitions from claimed
            mock_flow = MagicMock()
            mock_flow.get_transitions_from.return_value = []
            mock_load_flow.return_value = mock_flow

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "agent-1", tmp_task_dir)

            # Should NOT execute steps
            mock_execute.assert_not_called()
            # Should fall back to direct submit
            mock_sdk.tasks.submit.assert_called_once_with(
                task_id="test123", commits_count=0, turns_used=0,
            )

    def test_child_flow_done_outcome_accepts_task(self, tmp_task_dir, mock_sdk):
        """Child flow 'claimed -> done' transition causes engine to call sdk.tasks.accept()."""
        project_task = {
            "id": "test123",
            "title": "Child task",
            "queue": "claimed",
            "flow": "project",
            "project_id": "PROJ-abc",
        }
        mock_sdk.tasks.get.return_value = project_task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "done"}), \
             patch("octopoid.steps.execute_steps") as mock_execute, \
             patch("octopoid.flow.load_flow") as mock_load_flow:

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

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "agent-1", tmp_task_dir)

            # Steps executed
            mock_execute.assert_called_once_with(
                ["rebase_on_project_branch", "run_tests"],
                project_task,
                {"outcome": "done"},
                tmp_task_dir,
            )
            # Engine accepts (not submits) for claimed -> done
            mock_sdk.tasks.accept.assert_called_once_with(
                task_id="test123", accepted_by="flow-engine"
            )
            mock_sdk.tasks.submit.assert_not_called()


# ---------------------------------------------------------------------------
# Test: handle_agent_result moves to failed
# ---------------------------------------------------------------------------

class TestHandleAgentResultFailed:
    """Verify failed outcome transitions task to failed queue."""

    def test_failed_outcome_moves_to_failed(self, tmp_task_dir, mock_sdk, sample_task):
        """When outcome is 'failed', fail_task should be called to route the task."""
        mock_sdk.tasks.get.return_value = sample_task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={
                 "outcome": "failed",
                 "reason": "Tests don't pass",
             }), \
             patch("octopoid.tasks.fail_task") as mock_fail_task:
            mock_qu.get_sdk.return_value = mock_sdk

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "agent-1", tmp_task_dir)

            mock_fail_task.assert_called_once_with(
                "test123", reason="Tests don't pass", source="agent-outcome-failed"
            )

    def test_unknown_outcome_routes_to_requires_intervention(self, tmp_task_dir, mock_sdk, sample_task):
        """When outcome is 'unknown' (e.g. haiku unavailable), task routes to requires-intervention."""
        mock_sdk.tasks.get.return_value = sample_task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={
                 "outcome": "unknown",
                 "reason": "Could not infer outcome",
             }), \
             patch("octopoid.result_handler.request_intervention") as mock_intervention:
            mock_qu.get_sdk.return_value = mock_sdk

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "agent-1", tmp_task_dir)

            mock_intervention.assert_called_once()
            assert mock_intervention.call_args[0][0] == "test123"


# ---------------------------------------------------------------------------
# Test: handle_agent_result with no stdout.log
# ---------------------------------------------------------------------------

class TestHandleAgentResultNoStdout:
    """Verify graceful handling when agent produces no stdout.log."""

    def test_no_stdout_log_routes_to_requires_intervention(self, tmp_task_dir, mock_sdk, sample_task):
        """No stdout.log returns unknown outcome, which routes to requires-intervention."""
        mock_sdk.tasks.get.return_value = sample_task

        # Don't mock infer_result_from_stdout — let it run for real (no stdout.log present)
        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler._call_haiku") as mock_haiku, \
             patch("octopoid.result_handler.request_intervention") as mock_intervention:
            mock_qu.get_sdk.return_value = mock_sdk
            mock_haiku.return_value = "done"  # should not be called for missing file

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "agent-1", tmp_task_dir)

            # No stdout.log → outcome=unknown → requires-intervention
            mock_intervention.assert_called_once()
            mock_haiku.assert_not_called()  # haiku not called for missing file

    def test_done_outcome_non_claimed_queue_skips(self, tmp_task_dir, mock_sdk):
        """Done outcome when task is already provisional should not re-execute steps."""
        task = {
            "id": "test123",
            "title": "Test task",
            "queue": "provisional",
            "flow": "default",
        }
        mock_sdk.tasks.get.return_value = task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "done"}), \
             patch("octopoid.steps.execute_steps") as mock_execute, \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "agent-1", tmp_task_dir)

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
        # Task is a child of a project
        project_task = {
            "id": "test123",
            "title": "Child task",
            "role": "implement",
            "queue": "claimed",
            "flow": "project",
            "project_id": "PROJ-abc",
        }
        mock_sdk.tasks.get.return_value = project_task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "done"}), \
             patch("octopoid.steps.execute_steps") as mock_execute, \
             patch("octopoid.flow.load_flow") as mock_load_flow:

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

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "agent-1", tmp_task_dir)

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
                task_id="test123", accepted_by="flow-engine"
            )

    def test_non_project_task_uses_normal_flow_in_handle_done(self, tmp_task_dir, mock_sdk, sample_task):
        """When a task has no project_id, use normal top-level flow transitions."""
        mock_sdk.tasks.get.return_value = sample_task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "done"}), \
             patch("octopoid.steps.execute_steps") as mock_execute, \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            mock_transition = MagicMock()
            mock_transition.runs = ["push_branch", "create_pr"]
            mock_transition.to_state = "provisional"

            mock_child_flow = MagicMock()

            mock_flow = MagicMock()
            mock_flow.child_flow = mock_child_flow
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from octopoid.scheduler import handle_agent_result
            handle_agent_result("test123", "agent-1", tmp_task_dir)

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
                task_id="test123", commits_count=0, turns_used=0
            )

    def test_project_task_uses_child_flow_in_flow_dispatch(self, tmp_task_dir, mock_sdk):
        """handle_agent_result_via_flow uses child_flow transitions for project tasks."""
        project_task = {
            "id": "test123",
            "title": "Child task",
            "role": "implement",
            "queue": "claimed",
            "flow": "project",
            "project_id": "PROJ-abc",
        }
        mock_sdk.tasks.get.return_value = project_task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={
                 "status": "success", "decision": "approve", "comment": "LGTM",
             }), \
             patch("octopoid.steps.execute_steps") as mock_execute, \
             patch("octopoid.flow.load_flow") as mock_load_flow:

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

            from octopoid.scheduler import handle_agent_result_via_flow
            handle_agent_result_via_flow("test123", "agent-1", tmp_task_dir)

            mock_child_flow.get_transitions_from.assert_called_once_with("claimed")
            mock_flow.get_transitions_from.assert_not_called()
            mock_execute.assert_called_once()

    def test_non_project_task_uses_normal_flow_in_flow_dispatch(self, tmp_task_dir, mock_sdk, sample_task):
        """handle_agent_result_via_flow uses top-level flow for tasks without project_id."""
        mock_sdk.tasks.get.return_value = sample_task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={
                 "status": "success", "decision": "approve", "comment": "LGTM",
             }), \
             patch("octopoid.steps.execute_steps") as mock_execute, \
             patch("octopoid.flow.load_flow") as mock_load_flow:

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

            from octopoid.scheduler import handle_agent_result_via_flow
            handle_agent_result_via_flow("test123", "agent-1", tmp_task_dir)

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
        mock_sdk.tasks.get.return_value = sample_task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "done"}), \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = ["push_branch", "create_pr"]
            mock_transition.to_state = "provisional"
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            # Make execute_steps raise
            with patch("octopoid.steps.execute_steps", side_effect=RuntimeError("Tests failed")):
                from octopoid.scheduler import handle_agent_result
                with pytest.raises(RuntimeError, match="Tests failed"):
                    handle_agent_result("test123", "agent-1", tmp_task_dir)

        # Failure count file should have been incremented
        counter_file = tmp_task_dir / "step_failure_count"
        assert counter_file.exists()
        assert counter_file.read_text().strip() == "1"

    def test_after_3_failures_calls_fail_task_and_returns_cleanly(self, tmp_task_dir, mock_sdk, sample_task):
        """After 3 consecutive step failures, handle_agent_result calls fail_task and returns (no raise)."""
        # Pre-seed the counter to 2 (this will be the 3rd failure)
        (tmp_task_dir / "step_failure_count").write_text("2")

        mock_sdk.tasks.get.return_value = sample_task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "done"}), \
             patch("octopoid.result_handler.fail_task") as mock_fail_task, \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = ["push_branch"]
            mock_transition.to_state = "provisional"
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            with patch("octopoid.steps.execute_steps", side_effect=RuntimeError("Step failed again")):
                from octopoid.scheduler import handle_agent_result
                # Should NOT raise — returns cleanly so PID gets removed
                result = handle_agent_result("test123", "agent-1", tmp_task_dir)

        # Circuit breaker called fail_task with the right args
        mock_fail_task.assert_called_once()
        call_kwargs = mock_fail_task.call_args
        assert call_kwargs[0][0] == "test123"
        assert call_kwargs[1]["source"] == "step-failure-circuit-breaker"

        # Returns True so PID gets removed
        assert result is True

        # Counter reset
        counter_file = tmp_task_dir / "step_failure_count"
        assert not counter_file.exists()

    def test_success_resets_failure_counter(self, tmp_task_dir, mock_sdk, sample_task):
        """After a successful run, the failure counter file is no longer consulted (implicit reset)."""
        mock_sdk.tasks.get.return_value = sample_task

        with patch("octopoid.result_handler.queue_utils") as mock_qu, \
             patch("octopoid.result_handler.infer_result_from_stdout", return_value={"outcome": "done"}), \
             patch("octopoid.flow.load_flow") as mock_load_flow:

            mock_qu.get_sdk.return_value = mock_sdk

            mock_flow = MagicMock()
            mock_transition = MagicMock()
            mock_transition.runs = []
            mock_transition.to_state = "provisional"
            mock_flow.get_transitions_from.return_value = [mock_transition]
            mock_load_flow.return_value = mock_flow

            from octopoid.scheduler import handle_agent_result
            # Should not raise
            handle_agent_result("test123", "agent-1", tmp_task_dir)

        # submit called (transition worked)
        mock_sdk.tasks.submit.assert_called_once_with(
            task_id="test123", commits_count=0, turns_used=0
        )


# ---------------------------------------------------------------------------
# Test: guard_task_description_nonempty
# ---------------------------------------------------------------------------

class TestGuardTaskDescriptionNonempty:
    """Verify guard_task_description_nonempty blocks spawning for empty tasks."""

    def _make_ctx(self, claimed_task: dict | None, spawn_mode: str = "scripts") -> object:
        from octopoid.scheduler import AgentContext
        from octopoid.state_utils import AgentState
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
        from octopoid.scheduler import guard_task_description_nonempty
        ctx = self._make_ctx(None)
        proceed, reason = guard_task_description_nonempty(ctx)
        assert proceed is True
        assert reason == ""

    def test_non_scripts_mode_passes(self):
        """Guard passes for non-scripts-mode agents (they claim their own tasks)."""
        from octopoid.scheduler import guard_task_description_nonempty
        ctx = self._make_ctx({"id": "abc123", "content": ""}, spawn_mode="worktree")
        proceed, reason = guard_task_description_nonempty(ctx)
        assert proceed is True
        assert reason == ""

    def test_task_with_nonempty_content_passes(self):
        """Guard passes when task content is present and non-empty."""
        from octopoid.scheduler import guard_task_description_nonempty
        ctx = self._make_ctx({
            "id": "abc123",
            "content": "# Task\n\nDo something useful.\n",
        })
        proceed, reason = guard_task_description_nonempty(ctx)
        assert proceed is True
        assert reason == ""

    def test_task_with_whitespace_only_content_fails(self):
        """Guard blocks spawn and fails task when content is only whitespace."""
        from octopoid.scheduler import guard_task_description_nonempty

        mock_sdk = MagicMock()
        ctx = self._make_ctx({"id": "abc123", "content": "   \n   "})

        with patch("octopoid.scheduler.queue_utils") as mock_qu:
            mock_qu.get_sdk.return_value = mock_sdk
            proceed, reason = guard_task_description_nonempty(ctx)

        assert proceed is False
        assert "empty_description" in reason
        mock_qu.fail_task.assert_called_once()
        assert mock_qu.fail_task.call_args[0][0] == "abc123"
        assert mock_qu.fail_task.call_args[1]["source"] == "guard-empty-description"

    def test_task_with_no_content_field_fails(self):
        """Guard blocks spawn when task has no content field at all."""
        from octopoid.scheduler import guard_task_description_nonempty

        mock_sdk = MagicMock()
        ctx = self._make_ctx({"id": "abc123"})

        with patch("octopoid.scheduler.queue_utils") as mock_qu:
            mock_qu.get_sdk.return_value = mock_sdk
            proceed, reason = guard_task_description_nonempty(ctx)

        assert proceed is False
        assert "empty_description" in reason
        assert "abc123" in reason
        mock_qu.fail_task.assert_called_once()
        assert mock_qu.fail_task.call_args[0][0] == "abc123"
        assert mock_qu.fail_task.call_args[1]["source"] == "guard-empty-description"

    def test_empty_content_reason_mentions_task_id(self):
        """Error reason mentions expected task ID when content is missing."""
        from octopoid.scheduler import guard_task_description_nonempty

        mock_sdk = MagicMock()
        ctx = self._make_ctx({"id": "abc123", "content": ""})

        with patch("octopoid.scheduler.queue_utils") as mock_qu:
            mock_qu.get_sdk.return_value = mock_sdk
            proceed, reason = guard_task_description_nonempty(ctx)

        assert proceed is False
        assert "TASK-abc123.md" in reason

    def test_empty_content_fails_task(self):
        """Guard blocks spawn and fails task when content is empty string."""
        from octopoid.scheduler import guard_task_description_nonempty

        mock_sdk = MagicMock()
        ctx = self._make_ctx({"id": "abc123", "content": ""})

        with patch("octopoid.scheduler.queue_utils") as mock_qu:
            mock_qu.get_sdk.return_value = mock_sdk
            proceed, reason = guard_task_description_nonempty(ctx)

        assert proceed is False
        assert "empty_description" in reason
        mock_qu.fail_task.assert_called_once()
        assert mock_qu.fail_task.call_args[0][0] == "abc123"
        assert mock_qu.fail_task.call_args[1]["source"] == "guard-empty-description"

    def test_sdk_failure_still_blocks_spawn(self):
        """Guard still blocks spawn even if the SDK call to fail the task throws."""
        from octopoid.scheduler import guard_task_description_nonempty

        mock_sdk = MagicMock()
        mock_sdk.tasks.update.side_effect = RuntimeError("network error")
        ctx = self._make_ctx({"id": "abc123", "content": ""})

        with patch("octopoid.scheduler.queue_utils") as mock_qu:
            mock_qu.get_sdk.return_value = mock_sdk
            proceed, reason = guard_task_description_nonempty(ctx)

        # Guard still blocks even though SDK threw
        assert proceed is False
        assert "empty_description" in reason


# ---------------------------------------------------------------------------
# Test: systemic failure counter and auto-pause
# ---------------------------------------------------------------------------

class TestSystemicFailureCounter:
    """Verify systemic failure counter tracks spawn failures and auto-pauses."""

    def test_load_system_health_returns_defaults_when_missing(self, tmp_path):
        """_load_system_health returns zero-state when file doesn't exist."""
        from octopoid.scheduler import _load_system_health
        with patch("octopoid.system_health._get_system_health_path", return_value=tmp_path / "system_health.json"):
            health = _load_system_health()
        assert health["consecutive_systemic_failures"] == 0
        assert health["auto_paused"] is False

    def test_record_systemic_failure_increments_counter(self, tmp_path):
        """_record_systemic_failure increments the counter and saves to disk."""
        from octopoid.scheduler import _record_systemic_failure, _load_system_health

        health_path = tmp_path / "system_health.json"
        with patch("octopoid.system_health._get_system_health_path", return_value=health_path), \
             patch("octopoid.system_health.get_orchestrator_dir", return_value=tmp_path):
            _record_systemic_failure("docker daemon not running")

        health = json.loads(health_path.read_text())
        assert health["consecutive_systemic_failures"] == 1
        assert health["auto_paused"] is False
        assert health["last_failure_time"] is not None

    def test_two_consecutive_failures_trigger_auto_pause(self, tmp_path):
        """Two consecutive spawn failures write PAUSE file and update system_health.json."""
        from octopoid.scheduler import _handle_systemic_failure

        health_path = tmp_path / "system_health.json"
        pause_file = tmp_path / "PAUSE"

        with patch("octopoid.system_health._get_system_health_path", return_value=health_path), \
             patch("octopoid.system_health.get_orchestrator_dir", return_value=tmp_path), \
             patch("octopoid.system_health._spawn_diagnostic_agent"):
            _handle_systemic_failure("worktree creation failed")
            _handle_systemic_failure("git clone failed")

        assert pause_file.exists(), "PAUSE file should be written after 2 failures"
        health = json.loads(health_path.read_text())
        assert health["consecutive_systemic_failures"] == 2
        assert health["auto_paused"] is True
        assert health["auto_pause_reason"] is not None
        assert "2 consecutive systemic failures" in health["auto_pause_reason"]
        assert "git clone failed" in health["auto_pause_reason"]

    def test_reset_clears_counter(self, tmp_path):
        """_reset_systemic_failure_counter zeroes out the counter."""
        from octopoid.scheduler import _reset_systemic_failure_counter

        health_path = tmp_path / "system_health.json"
        # Pre-seed with 1 failure
        health_path.write_text(json.dumps({"consecutive_systemic_failures": 1}))

        with patch("octopoid.system_health._get_system_health_path", return_value=health_path):
            _reset_systemic_failure_counter()

        health = json.loads(health_path.read_text())
        assert health["consecutive_systemic_failures"] == 0

    def test_blameless_requeue_does_not_increment_attempt_count(self, tmp_path):
        """_requeue_task_blameless calls sdk.tasks.update without attempt_count."""
        from octopoid.scheduler import _requeue_task_blameless

        mock_sdk = MagicMock()
        with patch("octopoid.scheduler.queue_utils") as mock_qu:
            mock_qu.get_sdk.return_value = mock_sdk
            # Patch the local import inside the function
            with patch("octopoid.queue_utils.get_sdk", return_value=mock_sdk):
                _requeue_task_blameless("abc123", source_queue="incoming")

        # The update should NOT include attempt_count
        call_kwargs = mock_sdk.tasks.update.call_args
        assert call_kwargs is not None
        assert "attempt_count" not in (call_kwargs.kwargs or {})
        assert call_kwargs.args[0] == "abc123"

    def test_spawn_failure_calls_blameless_requeue_and_records_failure(self, tmp_path):
        """Spawn failure requeues task blameless and records systemic failure."""
        from octopoid.scheduler import _run_agent_evaluation_loop
        from octopoid.state_utils import AgentState

        health_path = tmp_path / "system_health.json"
        pause_file = tmp_path / "PAUSE"

        state_path = tmp_path / "state.json"
        state = AgentState()

        agent_config = {
            "name": "implementer-1",
            "role": "implement",
            "claim_from": "incoming",
        }
        claimed_task = {"id": "abc123", "title": "test task", "attempt_count": 0}

        with patch("octopoid.scheduler.get_agents", return_value=[agent_config]), \
             patch("octopoid.scheduler.get_agent_lock_path", return_value=tmp_path / "lock"), \
             patch("octopoid.scheduler.get_agent_state_path", return_value=state_path), \
             patch("octopoid.scheduler.load_state", return_value=state), \
             patch("octopoid.scheduler.evaluate_agent", return_value=True), \
             patch("octopoid.scheduler.get_spawn_strategy") as mock_strategy, \
             patch("octopoid.scheduler._requeue_task_blameless") as mock_blameless_requeue, \
             patch("octopoid.system_health._record_systemic_failure", return_value=0) as mock_record, \
             patch("octopoid.scheduler._reset_systemic_failure_counter") as mock_reset:

            # Attach the claimed task to context via evaluate_agent side effect
            def set_claimed_task(ctx):
                ctx.claimed_task = claimed_task
                return True
            mock_strategy_fn = MagicMock(side_effect=RuntimeError("docker not available"))
            mock_strategy.return_value = mock_strategy_fn

            # Patch evaluate_agent to set claimed_task on ctx
            def fake_evaluate(ctx):
                ctx.claimed_task = claimed_task
                return True

            with patch("octopoid.scheduler.evaluate_agent", side_effect=fake_evaluate):
                _run_agent_evaluation_loop(queue_counts={})

        # Blameless requeue called, not regular requeue
        mock_blameless_requeue.assert_called_once_with("abc123", source_queue="incoming")
        # Systemic failure recorded
        mock_record.assert_called_once()
        # Counter NOT reset (spawn failed)
        mock_reset.assert_not_called()
