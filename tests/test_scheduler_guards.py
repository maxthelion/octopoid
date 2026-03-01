"""Tests for scheduler guard bug fixes.

Covers:
- check_and_update_finished_agents: infers result from stdout.log for pure-function agents
- guard_claim_task dedup: prevents two pool instances from working the same task
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from octopoid.scheduler import (
    AgentContext,
    check_and_update_finished_agents,
    guard_claim_task,
)
from octopoid.state_utils import AgentState


# =============================================================================
# check_and_update_finished_agents infers result from stdout.log for pure-function agents
# =============================================================================


class TestCheckAndUpdateFinishedAgents:
    """check_and_update_finished_agents uses blueprint PID tracking via running_pids.json."""

    def _make_pids_dict(
        self, pid: int, task_id: str, instance_name: str
    ) -> dict:
        """Return a {pid: info} dict representing one tracked instance."""
        return {
            pid: {
                "task_id": task_id,
                "started_at": "2026-01-01T00:00:00+00:00",
                "instance_name": instance_name,
            }
        }

    def _make_agents_dir(self, tmp_path: Path, blueprint_name: str) -> tuple[Path, Path]:
        """Create agents_dir with a blueprint subdir containing running_pids.json."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        blueprint_dir = agents_dir / blueprint_name
        blueprint_dir.mkdir(exist_ok=True)
        # Write an empty sentinel file — real data is provided via mocked load_blueprint_pids
        (blueprint_dir / "running_pids.json").write_text("{}")
        return agents_dir, blueprint_dir

    def test_skips_dir_without_running_pids_json(self, tmp_path):
        """Directories without running_pids.json are silently skipped."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "implementer").mkdir()  # no running_pids.json

        with (
            patch("octopoid.scheduler.get_agents_runtime_dir", return_value=agents_dir),
            patch("octopoid.scheduler.get_agents", return_value=[]),
            patch("octopoid.scheduler.handle_agent_result") as mock_handle,
        ):
            check_and_update_finished_agents()

        mock_handle.assert_not_called()

    def test_dead_pid_triggers_handle_agent_result(self, tmp_path):
        """Dead PID with task_id triggers handle_agent_result for incoming queue."""
        agents_dir, _ = self._make_agents_dir(tmp_path, "implementer")
        tasks_dir = tmp_path / "tasks"
        task_id = "TASK-abc"
        (tasks_dir / task_id).mkdir(parents=True)

        pids_data = self._make_pids_dict(12345, task_id, "implementer-1")

        with (
            patch("octopoid.scheduler.get_agents_runtime_dir", return_value=agents_dir),
            patch("octopoid.scheduler.get_agents", return_value=[
                {"blueprint_name": "implementer", "claim_from": "incoming"}
            ]),
            patch("octopoid.scheduler.get_tasks_dir", return_value=tasks_dir),
            patch("octopoid.scheduler.load_blueprint_pids", return_value=pids_data),
            patch("octopoid.scheduler.save_blueprint_pids"),
            patch("octopoid.scheduler.is_process_running", return_value=False),
            patch("octopoid.scheduler.handle_agent_result") as mock_handle,
            patch("octopoid.scheduler.handle_agent_result_via_flow") as mock_flow,
        ):
            check_and_update_finished_agents()

        mock_handle.assert_called_once_with(task_id, "implementer-1", tasks_dir / task_id)
        mock_flow.assert_not_called()

    def test_dead_pid_triggers_flow_for_non_incoming_queue(self, tmp_path):
        """Dead PID with claim_from=provisional uses handle_agent_result_via_flow."""
        agents_dir, _ = self._make_agents_dir(tmp_path, "gatekeeper")
        tasks_dir = tmp_path / "tasks"
        task_id = "TASK-prov"
        (tasks_dir / task_id).mkdir(parents=True)

        pids_data = self._make_pids_dict(99999, task_id, "gatekeeper-1")

        with (
            patch("octopoid.scheduler.get_agents_runtime_dir", return_value=agents_dir),
            patch("octopoid.scheduler.get_agents", return_value=[
                {"blueprint_name": "gatekeeper", "claim_from": "provisional"}
            ]),
            patch("octopoid.scheduler.get_tasks_dir", return_value=tasks_dir),
            patch("octopoid.scheduler.load_blueprint_pids", return_value=pids_data),
            patch("octopoid.scheduler.save_blueprint_pids"),
            patch("octopoid.scheduler.is_process_running", return_value=False),
            patch("octopoid.scheduler.handle_agent_result") as mock_handle,
            patch("octopoid.scheduler.handle_agent_result_via_flow") as mock_flow,
        ):
            check_and_update_finished_agents()

        mock_flow.assert_called_once_with(task_id, "gatekeeper-1", tasks_dir / task_id, expected_queue="provisional")
        mock_handle.assert_not_called()

    def test_dead_pid_removed_from_tracking(self, tmp_path):
        """Dead PIDs are removed from pool tracking (save_blueprint_pids called without dead PID)."""
        agents_dir, _ = self._make_agents_dir(tmp_path, "implementer")
        tasks_dir = tmp_path / "tasks"
        task_id = "TASK-abc"
        (tasks_dir / task_id).mkdir(parents=True)

        pids_data = self._make_pids_dict(12345, task_id, "implementer-1")
        saved_args: list[tuple] = []

        with (
            patch("octopoid.scheduler.get_agents_runtime_dir", return_value=agents_dir),
            patch("octopoid.scheduler.get_agents", return_value=[
                {"blueprint_name": "implementer", "claim_from": "incoming"}
            ]),
            patch("octopoid.scheduler.get_tasks_dir", return_value=tasks_dir),
            patch("octopoid.scheduler.load_blueprint_pids", return_value=pids_data),
            patch("octopoid.scheduler.save_blueprint_pids",
                  side_effect=lambda name, p: saved_args.append((name, p))),
            patch("octopoid.scheduler.is_process_running", return_value=False),
            patch("octopoid.scheduler.handle_agent_result"),
        ):
            check_and_update_finished_agents()

        assert len(saved_args) == 1
        saved_name, saved_pids = saved_args[0]
        assert saved_name == "implementer"
        assert 12345 not in saved_pids

    def test_live_pid_not_removed(self, tmp_path):
        """Live PIDs are NOT removed from pool tracking."""
        agents_dir, _ = self._make_agents_dir(tmp_path, "implementer")
        pids_data = self._make_pids_dict(12345, "TASK-alive", "implementer-1")

        with (
            patch("octopoid.scheduler.get_agents_runtime_dir", return_value=agents_dir),
            patch("octopoid.scheduler.get_agents", return_value=[
                {"blueprint_name": "implementer", "claim_from": "incoming"}
            ]),
            patch("octopoid.scheduler.load_blueprint_pids", return_value=pids_data),
            patch("octopoid.scheduler.save_blueprint_pids") as mock_save,
            patch("octopoid.scheduler.is_process_running", return_value=True),
            patch("octopoid.scheduler.handle_agent_result") as mock_handle,
        ):
            check_and_update_finished_agents()

        mock_handle.assert_not_called()
        mock_save.assert_not_called()

    def test_no_task_id_skips_result_handling(self, tmp_path):
        """When PID entry has empty task_id, result handling is skipped."""
        agents_dir, _ = self._make_agents_dir(tmp_path, "proposer")
        # PID with empty task_id (e.g. lightweight agents without tasks)
        pids_data = {12345: {"task_id": "", "started_at": "...", "instance_name": "proposer-1"}}

        with (
            patch("octopoid.scheduler.get_agents_runtime_dir", return_value=agents_dir),
            patch("octopoid.scheduler.get_agents", return_value=[
                {"blueprint_name": "proposer", "claim_from": "incoming"}
            ]),
            patch("octopoid.scheduler.load_blueprint_pids", return_value=pids_data),
            patch("octopoid.scheduler.save_blueprint_pids"),
            patch("octopoid.scheduler.is_process_running", return_value=False),
            patch("octopoid.scheduler.handle_agent_result") as mock_handle,
        ):
            check_and_update_finished_agents()

        mock_handle.assert_not_called()


# =============================================================================
# guard_claim_task dedup: prevent duplicate instances on the same task
# =============================================================================


def _make_scripts_ctx(agent_name: str = "gatekeeper", blueprint_name: str = "gatekeeper") -> AgentContext:
    """Build a minimal AgentContext for a scripts-mode agent."""
    return AgentContext(
        agent_config={
            "spawn_mode": "scripts",
            "claim_from": "provisional",
            "blueprint_name": blueprint_name,
        },
        agent_name=agent_name,
        role="gatekeeper",
        interval=60,
        state=AgentState(),
        state_path=Path("/tmp/fake_state.json"),
    )


class TestGuardClaimTaskDedup:
    """guard_claim_task must not allow two pool instances to work the same task."""

    def test_skips_if_task_already_active(self):
        """If claimed task is already being worked on, skip without requeuing."""
        task = {"id": "TASK-projfix-2", "queue": "provisional"}
        ctx = _make_scripts_ctx()

        with (
            patch("octopoid.scheduler.claim_and_prepare_task", return_value=task),
            patch("octopoid.scheduler.get_active_task_ids", return_value={"TASK-projfix-2"}),
            patch("octopoid.scheduler.logger"),
        ):
            proceed, reason = guard_claim_task(ctx)

        assert proceed is False
        assert "duplicate_task" in reason
        assert "TASK-projfix-2" in reason
        assert ctx.claimed_task is None

    def test_proceeds_if_task_not_active(self):
        """If claimed task is not already active, allow spawn."""
        task = {"id": "TASK-new", "queue": "provisional"}
        ctx = _make_scripts_ctx()

        with (
            patch("octopoid.scheduler.claim_and_prepare_task", return_value=task),
            patch("octopoid.scheduler.get_active_task_ids", return_value=set()),
            patch("octopoid.scheduler.logger"),
            patch("octopoid.scheduler._requeue_task") as mock_requeue,
        ):
            proceed, reason = guard_claim_task(ctx)

        assert proceed is True
        assert reason == ""
        mock_requeue.assert_not_called()
        assert ctx.claimed_task == task

    def test_allows_different_task_from_same_blueprint(self):
        """Instance-2 claiming TASK-b is fine if instance-1 is on TASK-a."""
        task = {"id": "TASK-b", "queue": "provisional"}
        ctx = _make_scripts_ctx()

        with (
            patch("octopoid.scheduler.claim_and_prepare_task", return_value=task),
            patch("octopoid.scheduler.get_active_task_ids", return_value={"TASK-a"}),
            patch("octopoid.scheduler.logger"),
            patch("octopoid.scheduler._requeue_task") as mock_requeue,
        ):
            proceed, reason = guard_claim_task(ctx)

        assert proceed is True
        mock_requeue.assert_not_called()
        assert ctx.claimed_task == task

    def test_no_claim_returns_false_without_dedup_check(self):
        """When no task is available to claim, skip dedup check entirely."""
        ctx = _make_scripts_ctx()

        with (
            patch("octopoid.scheduler.claim_and_prepare_task", return_value=None),
            patch("octopoid.scheduler.get_active_task_ids") as mock_active,
            patch("octopoid.scheduler.logger"),
        ):
            proceed, reason = guard_claim_task(ctx)

        assert proceed is False
        assert reason == "no_task_to_claim"
        mock_active.assert_not_called()  # dedup not reached if nothing was claimed

    def test_worktree_mode_skips_dedup(self):
        """Non-scripts agents skip guard_claim_task entirely (including dedup)."""
        ctx = AgentContext(
            agent_config={"spawn_mode": "worktree", "blueprint_name": "implementer"},
            agent_name="implementer",
            role="implement",
            interval=60,
            state=AgentState(),
            state_path=Path("/tmp/fake_state.json"),
        )

        with (
            patch("octopoid.scheduler.claim_and_prepare_task") as mock_claim,
            patch("octopoid.scheduler.get_active_task_ids") as mock_active,
        ):
            proceed, reason = guard_claim_task(ctx)

        assert proceed is True
        mock_claim.assert_not_called()
        mock_active.assert_not_called()


# =============================================================================
# guard_claim_task circuit breaker: intervention fixer deduplication
# =============================================================================


def _make_intervention_ctx() -> AgentContext:
    """Build a minimal AgentContext for a fixer/intervention agent."""
    return AgentContext(
        agent_config={
            "spawn_mode": "scripts",
            "claim_from": "intervention",
            "blueprint_name": "fixer",
        },
        agent_name="fixer",
        role="fix",
        interval=60,
        state=AgentState(),
        state_path=Path("/tmp/fake_state.json"),
    )


def _make_sdk(
    tasks: list[dict],
    messages_by_task: dict[str, list[dict]] | None = None,
) -> MagicMock:
    """Build a mock SDK with canned task list and message responses."""
    mock_sdk = MagicMock()
    mock_sdk.tasks.list.return_value = tasks

    messages_by_task = messages_by_task or {}

    def fake_request(method: str, path: str) -> dict:
        for task_id, msgs in messages_by_task.items():
            if task_id in path:
                return {"messages": msgs}
        return {"messages": []}

    mock_sdk._request.side_effect = fake_request
    return mock_sdk


class TestGuardClaimTaskCircuitBreaker:
    """Circuit breaker in guard_claim_task (intervention fixer path)."""

    def test_skips_task_already_in_failed_queue(self):
        """Tasks already in failed queue are skipped without re-firing the circuit breaker."""
        candidate = {"id": "TASK-cb-1", "queue": "failed", "needs_intervention": True}
        mock_sdk = _make_sdk([candidate])
        ctx = _make_intervention_ctx()

        with (
            patch("octopoid.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("octopoid.scheduler.get_active_task_ids", return_value=set()),
            patch("octopoid.scheduler.get_agents_runtime_dir", return_value=Path("/tmp/fake_agents")),
        ):
            proceed, reason = guard_claim_task(ctx)

        assert proceed is False
        assert reason == "no_task_to_claim"
        # Must not attempt to update or post messages for an already-failed task
        mock_sdk.tasks.update.assert_not_called()
        mock_sdk.messages.create.assert_not_called()

    def test_skips_task_with_existing_circuit_breaker_message(self):
        """If a circuit_breaker message already exists, skip the task — no duplicate fires."""
        candidate = {"id": "TASK-cb-2", "queue": "intervention", "needs_intervention": True}
        mock_sdk = _make_sdk(
            [candidate],
            messages_by_task={
                "TASK-cb-2": [
                    {"type": "intervention_reply"},
                    {"type": "intervention_reply"},
                    {"type": "intervention_reply"},
                    {"type": "circuit_breaker"},  # already fired
                ]
            },
        )
        ctx = _make_intervention_ctx()

        with (
            patch("octopoid.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("octopoid.scheduler.get_active_task_ids", return_value=set()),
            patch("octopoid.scheduler.get_agents_runtime_dir", return_value=Path("/tmp/fake_agents")),
        ):
            proceed, reason = guard_claim_task(ctx)

        assert proceed is False
        assert reason == "no_task_to_claim"
        # Must not call update or create another message
        mock_sdk.tasks.update.assert_not_called()
        mock_sdk.messages.create.assert_not_called()

    def test_fires_circuit_breaker_exactly_once(self):
        """First time a task exceeds MAX_FIXER_ATTEMPTS, the circuit breaker fires once."""
        candidate = {"id": "TASK-cb-3", "queue": "intervention", "needs_intervention": True}
        mock_sdk = _make_sdk(
            [candidate],
            messages_by_task={
                "TASK-cb-3": [
                    {"type": "intervention_reply"},
                    {"type": "intervention_reply"},
                    {"type": "intervention_reply"},
                    # No circuit_breaker message yet — should fire now
                ]
            },
        )
        ctx = _make_intervention_ctx()

        with (
            patch("octopoid.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("octopoid.scheduler.get_active_task_ids", return_value=set()),
            patch("octopoid.scheduler.get_agents_runtime_dir", return_value=Path("/tmp/fake_agents")),
        ):
            proceed, reason = guard_claim_task(ctx)

        assert proceed is False
        assert reason == "no_task_to_claim"
        mock_sdk.tasks.update.assert_called_once_with(
            "TASK-cb-3",
            queue="failed",
            needs_intervention=False,
            execution_notes=pytest.approx("Fixer circuit breaker: 3 attempts exhausted", abs=None),
        )
        mock_sdk.messages.create.assert_called_once()
        call_kwargs = mock_sdk.messages.create.call_args.kwargs
        assert call_kwargs["type"] == "circuit_breaker"
        assert call_kwargs["task_id"] == "TASK-cb-3"

    def test_update_failure_still_posts_message(self):
        """If sdk.tasks.update raises, the circuit_breaker message is still posted."""
        candidate = {"id": "TASK-cb-4", "queue": "intervention", "needs_intervention": True}
        mock_sdk = _make_sdk(
            [candidate],
            messages_by_task={
                "TASK-cb-4": [
                    {"type": "intervention_reply"},
                    {"type": "intervention_reply"},
                    {"type": "intervention_reply"},
                ]
            },
        )
        mock_sdk.tasks.update.side_effect = RuntimeError("server error")
        ctx = _make_intervention_ctx()

        with (
            patch("octopoid.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("octopoid.scheduler.get_active_task_ids", return_value=set()),
            patch("octopoid.scheduler.get_agents_runtime_dir", return_value=Path("/tmp/fake_agents")),
            patch("octopoid.scheduler.logger"),
        ):
            # Must not raise even if update fails
            proceed, reason = guard_claim_task(ctx)

        assert proceed is False
        # Message should still be attempted
        mock_sdk.messages.create.assert_called_once()

    def test_below_threshold_task_is_claimed(self):
        """A task with fewer than MAX_FIXER_ATTEMPTS should be claimed normally."""
        candidate = {"id": "TASK-cb-5", "queue": "intervention", "needs_intervention": True}
        mock_sdk = _make_sdk(
            [candidate],
            messages_by_task={
                "TASK-cb-5": [
                    {"type": "intervention_reply"},
                    {"type": "intervention_reply"},
                    # Only 2 attempts — below threshold of 3
                ]
            },
        )
        ctx = _make_intervention_ctx()

        with (
            patch("octopoid.scheduler.queue_utils.get_sdk", return_value=mock_sdk),
            patch("octopoid.scheduler.get_active_task_ids", return_value=set()),
            patch("octopoid.scheduler.get_agents_runtime_dir") as mock_agents_dir,
        ):
            mock_agents_dir.return_value = Path("/tmp/fake_agents")
            # Prevent file I/O
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                proceed, reason = guard_claim_task(ctx)

        assert proceed is True
        mock_sdk.tasks.update.assert_not_called()
        mock_sdk.messages.create.assert_not_called()
