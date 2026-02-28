"""Tests for the requires-intervention queue and fixer agent subsystem.

Covers:
- fail_task() routes to requires-intervention on first failure
- fail_task() routes to true failed when already in requires-intervention
- request_intervention() writes intervention_context.json
- execute_steps() writes step_progress.json
- handle_fixer_result() resumes flow on outcome=fixed
- handle_fixer_result() moves to true failed on outcome=failed
- _handle_fail_outcome() routes agent failures through intervention (not directly to failed)
- Structural invariant: no direct sdk.tasks.update(queue='failed') outside fail_task()
"""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# =============================================================================
# fail_task() routing
# =============================================================================


class TestFailTaskRouting:
    """fail_task routes based on the task's current queue."""

    def _make_sdk(self, current_queue: str) -> MagicMock:
        sdk = MagicMock()
        sdk.tasks.get.return_value = {"id": "TASK-1", "queue": current_queue}
        sdk.tasks.update.return_value = {"id": "TASK-1", "queue": "requires-intervention"}
        return sdk

    def test_first_failure_calls_request_intervention(self, tmp_path):
        """First failure (task in 'claimed') routes to requires-intervention."""
        sdk = self._make_sdk("claimed")
        task_dir = tmp_path / "TASK-1"
        task_dir.mkdir()

        with (
            patch("octopoid.tasks.get_sdk", return_value=sdk),
            patch("octopoid.config.get_tasks_dir", return_value=tmp_path),
            patch("octopoid.tasks.get_task_logger"),
            patch("octopoid.task_thread.post_message"),
        ):
            from octopoid.tasks import fail_task
            fail_task("TASK-1", reason="something broke", source="test-source")

        # Should update to requires-intervention (not failed)
        sdk.tasks.update.assert_called_once()
        call_kwargs = sdk.tasks.update.call_args
        assert call_kwargs[0][0] == "TASK-1"
        assert call_kwargs[1]["queue"] == "requires-intervention"

    def test_second_failure_routes_to_true_failed(self):
        """When task is already in requires-intervention, fail_task goes to true failed."""
        sdk = self._make_sdk("requires-intervention")
        sdk.tasks.update.return_value = {"id": "TASK-1", "queue": "failed"}

        with (
            patch("octopoid.tasks.get_sdk", return_value=sdk),
            patch("octopoid.tasks.get_task_logger"),
        ):
            from octopoid.tasks import fail_task
            fail_task("TASK-1", reason="fixer also failed", source="test-source")

        sdk.tasks.update.assert_called_once()
        call_kwargs = sdk.tasks.update.call_args
        assert call_kwargs[0][0] == "TASK-1"
        assert call_kwargs[1]["queue"] == "failed"

    def test_done_task_raises(self):
        """fail_task raises ValueError when task is already done."""
        sdk = self._make_sdk("done")

        with (
            patch("octopoid.tasks.get_sdk", return_value=sdk),
        ):
            from octopoid.tasks import fail_task
            with pytest.raises(ValueError, match="refusing to move"):
                fail_task("TASK-1", reason="too late", source="test-source")

        sdk.tasks.update.assert_not_called()


# =============================================================================
# request_intervention() context storage
# =============================================================================


class TestRequestIntervention:
    """request_intervention() writes intervention_context.json and posts audit message."""

    def test_writes_intervention_context_json(self, tmp_path):
        """intervention_context.json is written to the task directory."""
        task_id = "TASK-ctx"
        task_dir = tmp_path / task_id
        task_dir.mkdir()

        sdk = MagicMock()
        sdk.tasks.update.return_value = {"id": task_id, "queue": "requires-intervention"}

        with (
            patch("octopoid.tasks.get_sdk", return_value=sdk),
            patch("octopoid.config.get_tasks_dir", return_value=tmp_path),
            patch("octopoid.tasks.get_task_logger"),
            patch("octopoid.task_thread.post_message"),
        ):
            from octopoid.tasks import request_intervention
            request_intervention(
                task_id,
                reason="rebase failed",
                source="rebase-error",
                previous_queue="claimed",
            )

        ctx_file = task_dir / "intervention_context.json"
        assert ctx_file.exists()
        ctx = json.loads(ctx_file.read_text())
        assert ctx["previous_queue"] == "claimed"
        assert ctx["error_source"] == "rebase-error"
        assert "rebase failed" in ctx["error_message"]

    def test_reads_step_progress_json(self, tmp_path):
        """request_intervention picks up step progress written by execute_steps."""
        task_id = "TASK-progress"
        task_dir = tmp_path / task_id
        task_dir.mkdir()

        # Simulate execute_steps having run push_branch before failing on create_pr
        (task_dir / "step_progress.json").write_text(
            json.dumps({"completed": ["push_branch"], "failed": "create_pr"})
        )

        sdk = MagicMock()
        sdk.tasks.update.return_value = {}

        with (
            patch("octopoid.tasks.get_sdk", return_value=sdk),
            patch("octopoid.config.get_tasks_dir", return_value=tmp_path),
            patch("octopoid.tasks.get_task_logger"),
            patch("octopoid.task_thread.post_message"),
        ):
            from octopoid.tasks import request_intervention
            request_intervention(
                task_id,
                reason="step failed",
                source="step-error",
                previous_queue="claimed",
            )

        ctx = json.loads((task_dir / "intervention_context.json").read_text())
        assert ctx["steps_completed"] == ["push_branch"]
        assert ctx["step_that_failed"] == "create_pr"


# =============================================================================
# execute_steps() progress tracking
# =============================================================================


class TestExecuteStepsProgress:
    """execute_steps() writes step_progress.json after each step."""

    def test_writes_progress_on_success(self, tmp_path):
        """step_progress.json has completed steps and failed=null after success."""
        from octopoid.steps import STEP_REGISTRY, execute_steps

        original = STEP_REGISTRY.get("push_branch")
        try:
            STEP_REGISTRY["push_branch"] = lambda t, r, d: None
            execute_steps(["push_branch"], {}, {}, tmp_path)
        finally:
            if original is not None:
                STEP_REGISTRY["push_branch"] = original
            else:
                del STEP_REGISTRY["push_branch"]

        progress = json.loads((tmp_path / "step_progress.json").read_text())
        assert progress["completed"] == ["push_branch"]
        assert progress["failed"] is None

    def test_writes_progress_on_failure(self, tmp_path):
        """step_progress.json records failed step when a step raises."""
        from octopoid.steps import STEP_REGISTRY, execute_steps

        original_push = STEP_REGISTRY.get("push_branch")
        original_create = STEP_REGISTRY.get("create_pr")
        try:
            STEP_REGISTRY["push_branch"] = lambda t, r, d: None
            STEP_REGISTRY["create_pr"] = lambda t, r, d: (_ for _ in ()).throw(RuntimeError("PR failed"))

            with pytest.raises(RuntimeError):
                execute_steps(["push_branch", "create_pr"], {}, {}, tmp_path)
        finally:
            for name, orig in [("push_branch", original_push), ("create_pr", original_create)]:
                if orig is not None:
                    STEP_REGISTRY[name] = orig
                else:
                    STEP_REGISTRY.pop(name, None)

        progress = json.loads((tmp_path / "step_progress.json").read_text())
        assert progress["completed"] == ["push_branch"]
        assert progress["failed"] == "create_pr"

    def test_writes_progress_on_unknown_step(self, tmp_path):
        """step_progress.json records the unknown step name as failed."""
        from octopoid.steps import execute_steps

        with pytest.raises(ValueError):
            execute_steps(["unknown_step_xyz"], {}, {}, tmp_path)

        progress = json.loads((tmp_path / "step_progress.json").read_text())
        assert progress["completed"] == []
        assert progress["failed"] == "unknown_step_xyz"


# =============================================================================
# handle_fixer_result() — outcome=fixed
# =============================================================================


class TestHandleFixerResultFixed:
    """handle_fixer_result resumes the flow when fixer reports outcome=fixed."""

    def _make_task(self, queue: str = "requires-intervention") -> dict:
        return {"id": "TASK-fix", "queue": queue, "flow": "default"}

    def _write_intervention_context(
        self, task_dir: Path, previous_queue: str, steps_completed: list, step_that_failed: str
    ) -> None:
        ctx = {
            "previous_queue": previous_queue,
            "steps_completed": steps_completed,
            "step_that_failed": step_that_failed,
            "error_source": "test",
            "error_message": "test error",
        }
        (task_dir / "intervention_context.json").write_text(json.dumps(ctx))

    def test_fixed_outcome_resumes_remaining_steps(self, tmp_path):
        """outcome=fixed causes the remaining flow steps to execute (skipping completed)."""
        from octopoid.flow import Flow, Transition

        task_dir = tmp_path / "TASK-fix"
        task_dir.mkdir()
        self._write_intervention_context(
            task_dir,
            previous_queue="claimed",
            steps_completed=["push_branch"],
            step_that_failed="create_pr",
        )

        sdk = MagicMock()
        sdk.tasks.get.return_value = self._make_task()
        sdk.tasks.update.return_value = {}

        # Build a mock flow with a claimed→provisional transition that runs all 3 steps
        mock_transition = Transition(
            from_state="claimed",
            to_state="provisional",
            conditions=[],
            runs=["push_branch", "create_pr", "check_ci"],
        )
        mock_flow = Flow(name="default", description="", transitions=[mock_transition])

        steps_run = []
        fixed_result = {"outcome": "fixed", "diagnosis": "bad rebase", "fix_applied": "rebased"}

        with (
            patch("octopoid.result_handler.queue_utils.get_sdk", return_value=sdk),
            patch("octopoid.result_handler.infer_result_from_stdout", return_value=fixed_result),
            patch("octopoid.task_thread.post_message"),
            patch("octopoid.flow.load_flow", return_value=mock_flow),
            patch("octopoid.steps.execute_steps",
                  side_effect=lambda names, *_: steps_run.extend(names)),
            patch("octopoid.result_handler._perform_transition"),
        ):
            from octopoid.result_handler import handle_fixer_result
            result = handle_fixer_result("TASK-fix", "fixer-1", task_dir)

        assert result is True
        # Should run from create_pr onwards (not push_branch again)
        assert "create_pr" in steps_run
        assert "push_branch" not in steps_run

    def test_fixed_outcome_returns_true(self, tmp_path):
        """handle_fixer_result returns True (PID safe to remove) on fixed outcome."""
        task_dir = tmp_path / "TASK-fix"
        task_dir.mkdir()
        self._write_intervention_context(task_dir, "claimed", [], "")

        sdk = MagicMock()
        sdk.tasks.get.return_value = self._make_task()
        fixed_result = {"outcome": "fixed", "diagnosis": "fixed it", "fix_applied": "applied"}

        with (
            patch("octopoid.result_handler.queue_utils.get_sdk", return_value=sdk),
            patch("octopoid.result_handler.infer_result_from_stdout", return_value=fixed_result),
            patch("octopoid.task_thread.post_message"),
            patch("octopoid.steps.execute_steps"),
            patch("octopoid.result_handler._perform_transition"),
        ):
            from octopoid.result_handler import handle_fixer_result
            result = handle_fixer_result("TASK-fix", "fixer-1", task_dir)

        assert result is True


# =============================================================================
# handle_fixer_result() — outcome=failed (or anything else)
# =============================================================================


class TestHandleFixerResultFailed:
    """handle_fixer_result moves to true terminal failed on non-fixed outcomes."""

    def _write_intervention_context(self, task_dir: Path) -> None:
        ctx = {
            "previous_queue": "claimed",
            "steps_completed": [],
            "step_that_failed": "",
            "error_source": "test",
            "error_message": "test",
        }
        (task_dir / "intervention_context.json").write_text(json.dumps(ctx))

    def test_failed_outcome_moves_to_true_failed(self, tmp_path):
        """outcome=failed routes through fail_task() which goes to 'failed' from requires-intervention."""
        task_dir = tmp_path / "TASK-cant-fix"
        task_dir.mkdir()
        self._write_intervention_context(task_dir)

        # tasks_sdk is used by fail_task() internally via octopoid.tasks.get_sdk
        tasks_sdk = MagicMock()
        tasks_sdk.tasks.get.return_value = {"id": "TASK-cant-fix", "queue": "requires-intervention", "flow": "default"}
        tasks_sdk.tasks.update.return_value = {}

        # result_handler_sdk is used by handle_fixer_result() itself
        result_handler_sdk = MagicMock()
        result_handler_sdk.tasks.get.return_value = {"id": "TASK-cant-fix", "queue": "requires-intervention", "flow": "default"}

        failed_result = {"outcome": "failed", "diagnosis": "cannot fix this"}

        with (
            patch("octopoid.result_handler.queue_utils.get_sdk", return_value=result_handler_sdk),
            patch("octopoid.tasks.get_sdk", return_value=tasks_sdk),
            patch("octopoid.result_handler.infer_result_from_stdout", return_value=failed_result),
            patch("octopoid.task_thread.post_message"),
            patch("octopoid.tasks.get_task_logger"),
        ):
            from octopoid.result_handler import handle_fixer_result
            result = handle_fixer_result("TASK-cant-fix", "fixer-1", task_dir)

        assert result is True
        # fail_task() uses tasks_sdk to update queue to 'failed'
        tasks_sdk.tasks.update.assert_called_once()
        call_kwargs = tasks_sdk.tasks.update.call_args
        assert call_kwargs[0][0] == "TASK-cant-fix"
        assert call_kwargs[1]["queue"] == "failed"

    def test_failed_outcome_does_not_call_execute_steps(self, tmp_path):
        """outcome=failed never tries to resume flow steps."""
        task_dir = tmp_path / "TASK-cant-fix"
        task_dir.mkdir()
        self._write_intervention_context(task_dir)

        tasks_sdk = MagicMock()
        tasks_sdk.tasks.get.return_value = {"id": "TASK-cant-fix", "queue": "requires-intervention", "flow": "default"}
        tasks_sdk.tasks.update.return_value = {}

        result_handler_sdk = MagicMock()
        result_handler_sdk.tasks.get.return_value = {"id": "TASK-cant-fix", "queue": "requires-intervention", "flow": "default"}

        failed_result = {"outcome": "failed", "diagnosis": "cannot fix this"}

        with (
            patch("octopoid.result_handler.queue_utils.get_sdk", return_value=result_handler_sdk),
            patch("octopoid.tasks.get_sdk", return_value=tasks_sdk),
            patch("octopoid.result_handler.infer_result_from_stdout", return_value=failed_result),
            patch("octopoid.task_thread.post_message"),
            patch("octopoid.tasks.get_task_logger"),
            patch("octopoid.steps.execute_steps") as mock_steps,
        ):
            from octopoid.result_handler import handle_fixer_result
            handle_fixer_result("TASK-cant-fix", "fixer-1", task_dir)

        mock_steps.assert_not_called()

    def test_missing_task_returns_true(self, tmp_path):
        """When task is not found on server, returns True (stale PID removal)."""
        task_dir = tmp_path / "TASK-gone"
        task_dir.mkdir()
        self._write_intervention_context(task_dir)

        sdk = MagicMock()
        sdk.tasks.get.return_value = None
        failed_result = {"outcome": "failed", "diagnosis": "could not complete"}

        with (
            patch("octopoid.result_handler.queue_utils.get_sdk", return_value=sdk),
            patch("octopoid.result_handler.infer_result_from_stdout", return_value=failed_result),
        ):
            from octopoid.result_handler import handle_fixer_result
            result = handle_fixer_result("TASK-gone", "fixer-1", task_dir)

        assert result is True


# =============================================================================
# flow.py — requires-intervention in builtin states
# =============================================================================


class TestFlowRequiresIntervention:
    """requires-intervention is registered as a builtin state in the flow system."""

    def test_requires_intervention_in_all_states(self):
        """get_all_states() includes requires-intervention for any flow."""
        from octopoid.flow import Flow, Transition

        # Build a minimal flow with one transition
        flow = Flow(
            name="test",
            description="test flow",
            transitions=[
                Transition(from_state="incoming", to_state="claimed", conditions=[], runs=[]),
            ],
        )
        all_states = flow.get_all_states()
        assert "requires-intervention" in all_states

    def test_implicit_transitions_include_requires_intervention(self):
        """Implicit transitions include claimed→requires-intervention."""
        from octopoid.flow import _implicit_reverse_transitions

        implicit = _implicit_reverse_transitions([])
        froms_tos = [(t["from"], t["to"]) for t in implicit]
        assert ("claimed", "requires-intervention") in froms_tos
        assert ("requires-intervention", "failed") in froms_tos


# =============================================================================
# _handle_fail_outcome() — agent failure routing
# =============================================================================


class TestHandleFailOutcomeRouting:
    """_handle_fail_outcome() routes agent failures through intervention, not directly to failed."""

    def _make_sdk(self, current_queue: str) -> MagicMock:
        sdk = MagicMock()
        sdk.tasks.get.return_value = {"id": "TASK-1", "queue": current_queue}
        sdk.tasks.update.return_value = {"id": "TASK-1", "queue": "requires-intervention"}
        return sdk

    def test_agent_failure_routes_to_requires_intervention(self, tmp_path):
        """Agent outcome=failed causes task to go to requires-intervention, not directly to failed."""
        task_id = "TASK-fail"
        task_dir = tmp_path / task_id
        task_dir.mkdir()

        sdk = self._make_sdk("claimed")
        task = {"id": task_id, "queue": "claimed", "flow": "default"}

        with (
            patch("octopoid.tasks.get_sdk", return_value=sdk),
            patch("octopoid.config.get_tasks_dir", return_value=tmp_path),
            patch("octopoid.tasks.get_task_logger"),
            patch("octopoid.task_thread.post_message"),
        ):
            from octopoid.result_handler import _handle_fail_outcome
            result = _handle_fail_outcome(sdk, task_id, task, "agent reported failure", "claimed")

        assert result is True
        # Must route to requires-intervention, not to failed
        sdk.tasks.update.assert_called_once()
        call_args = sdk.tasks.update.call_args
        assert call_args[1]["queue"] == "requires-intervention"

    def test_agent_failure_does_not_call_failed_directly(self, tmp_path):
        """_handle_fail_outcome never calls sdk.tasks.update(queue='failed') directly."""
        task_id = "TASK-fail2"
        task_dir = tmp_path / task_id
        task_dir.mkdir()

        sdk = self._make_sdk("claimed")
        task = {"id": task_id, "queue": "claimed", "flow": "default"}

        failed_calls = []

        def capture_update(tid, **kwargs):
            if kwargs.get("queue") == "failed":
                failed_calls.append(kwargs)
            return {"id": tid, "queue": kwargs.get("queue", "unknown")}

        sdk.tasks.update.side_effect = capture_update

        with (
            patch("octopoid.tasks.get_sdk", return_value=sdk),
            patch("octopoid.config.get_tasks_dir", return_value=tmp_path),
            patch("octopoid.tasks.get_task_logger"),
            patch("octopoid.task_thread.post_message"),
        ):
            from octopoid.result_handler import _handle_fail_outcome
            _handle_fail_outcome(sdk, task_id, task, "agent error", "claimed")

        assert failed_calls == [], (
            "_handle_fail_outcome should not route directly to 'failed'; "
            f"got direct failed calls: {failed_calls}"
        )

    def test_terminal_queue_returns_true_without_transition(self):
        """When task is already in a terminal queue, _handle_fail_outcome removes stale PID."""
        sdk = MagicMock()
        task = {"id": "TASK-done", "queue": "done", "flow": "default"}

        from octopoid.result_handler import _handle_fail_outcome
        result = _handle_fail_outcome(sdk, "TASK-done", task, "too late", "done")

        assert result is True
        sdk.tasks.update.assert_not_called()

    def test_non_claimed_non_terminal_returns_false(self):
        """When task is in a non-terminal queue that isn't claimed, keep PID for retry."""
        sdk = MagicMock()
        task = {"id": "TASK-prov", "queue": "provisional", "flow": "default"}

        from octopoid.result_handler import _handle_fail_outcome
        result = _handle_fail_outcome(sdk, "TASK-prov", task, "odd state", "provisional")

        assert result is False
        sdk.tasks.update.assert_not_called()


# =============================================================================
# Structural invariant: no direct queue='failed' calls outside fail_task()
# =============================================================================


class TestNoDirectFailedCalls:
    """Structural invariant: only fail_task() may call sdk.tasks.update(queue='failed')."""

    def test_result_handler_has_no_direct_failed_update(self):
        """result_handler.py contains no direct sdk.tasks.update(...queue='failed'...) call."""
        import octopoid.result_handler as _mod

        source_file = Path(_mod.__file__)
        source = source_file.read_text()

        # Match patterns like: sdk.tasks.update(...queue="failed"...) or queue='failed'
        # We look for the update call with queue=failed together on a line or nearby.
        # The only legitimate occurrence of queue="failed" in result_handler is inside
        # a call to fail_task() — fail_task itself lives in tasks.py, not here.
        direct_failed_pattern = re.compile(
            r'\.tasks\.update\s*\([^)]*queue\s*=\s*["\']failed["\']',
            re.DOTALL,
        )
        matches = direct_failed_pattern.findall(source)
        assert matches == [], (
            "result_handler.py contains a direct sdk.tasks.update(queue='failed') call "
            f"outside fail_task(): {matches}"
        )

    def test_scheduler_has_no_direct_failed_update(self):
        """scheduler.py contains no direct sdk.tasks.update(queue='failed') call."""
        import octopoid.scheduler as _mod

        source_file = Path(_mod.__file__)
        source = source_file.read_text()

        direct_failed_pattern = re.compile(
            r'\.tasks\.update\s*\([^)]*queue\s*=\s*["\']failed["\']',
            re.DOTALL,
        )
        matches = direct_failed_pattern.findall(source)
        assert matches == [], (
            "scheduler.py contains a direct sdk.tasks.update(queue='failed') call "
            f"outside fail_task(): {matches}"
        )
