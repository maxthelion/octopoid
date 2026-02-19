"""Tests for PID lifecycle: verifying that only check_and_update_finished_agents
removes dead PIDs from running_pids.json.

The race condition: cleanup_dead_pids (called by the dashboard or
guard_pool_capacity) removes a dead PID from running_pids.json before
check_and_update_finished_agents can process the agent's result.

The fix: only check_and_update_finished_agents should remove dead PIDs.
guard_pool_capacity and _gather_agents must NOT call cleanup_dead_pids.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.pool import (
    cleanup_dead_pids,
    count_running_instances,
    load_blueprint_pids,
    save_blueprint_pids,
)


@pytest.fixture()
def runtime_dirs(tmp_path, monkeypatch):
    """Set up temp runtime directories for agents and tasks."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()

    monkeypatch.setattr(
        "orchestrator.pool.get_agents_runtime_dir", lambda: agents_dir
    )
    return agents_dir, tasks_dir


DEAD_PID = 99999
ALIVE_PID = 88888


def _fake_kill_dead_only(pid, sig):
    """Simulate DEAD_PID as dead, ALIVE_PID as alive."""
    if pid == DEAD_PID:
        raise ProcessLookupError
    # alive or unknown — succeed silently


def _setup_finished_agent(agents_dir, tasks_dir, task_id="TASK-test-1",
                          blueprint="implementer", pid=DEAD_PID):
    """Set up a finished agent: running_pids entry + result.json in task dir."""
    save_blueprint_pids(blueprint, {
        pid: {
            "task_id": task_id,
            "started_at": "2026-02-19T19:00:00+00:00",
            "instance_name": f"{blueprint}-1",
        }
    })

    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    result = {"outcome": "done"}
    (task_dir / "result.json").write_text(json.dumps(result))

    return task_dir


# ---------------------------------------------------------------------------
# Core invariant: cleanup_dead_pids removes PIDs without processing results
# ---------------------------------------------------------------------------


class TestCleanupEatsPids:
    """Verify that cleanup_dead_pids silently removes dead PIDs."""

    def test_cleanup_removes_dead_pid(self, runtime_dirs):
        """cleanup_dead_pids removes dead PIDs from running_pids.json.

        This is correct behavior for the function itself, but it's dangerous
        when called before check_and_update_finished_agents because the agent
        result is never processed.
        """
        agents_dir, tasks_dir = runtime_dirs
        _setup_finished_agent(agents_dir, tasks_dir)

        pids = load_blueprint_pids("implementer")
        assert DEAD_PID in pids

        with patch("orchestrator.pool.os.kill", side_effect=_fake_kill_dead_only):
            cleanup_dead_pids("implementer")

        pids_after = load_blueprint_pids("implementer")
        assert DEAD_PID not in pids_after


# ---------------------------------------------------------------------------
# FIX VERIFICATION: guard_pool_capacity must NOT call cleanup_dead_pids
# ---------------------------------------------------------------------------


class TestGuardPoolCapacityNoCleanup:
    """After fix: guard_pool_capacity should NOT call cleanup_dead_pids."""

    def test_guard_does_not_call_cleanup(self, runtime_dirs, monkeypatch):
        """guard_pool_capacity uses count_running_instances (which ignores dead
        PIDs) without calling cleanup_dead_pids."""
        agents_dir, tasks_dir = runtime_dirs
        _setup_finished_agent(agents_dir, tasks_dir)

        import orchestrator.scheduler as sched_mod

        # count_running_instances already ignores dead PIDs
        monkeypatch.setattr(sched_mod, "count_running_instances", MagicMock(return_value=0))

        ctx = sched_mod.AgentContext(
            agent_name="implementer",
            agent_config={"blueprint_name": "implementer", "max_instances": 2},
            role="implement",
            interval=60,
            state={},
            state_path=Path("/dev/null"),
        )

        sched_mod.guard_pool_capacity(ctx)

        # Dead PID should still be in running_pids.json — not cleaned up
        pids = load_blueprint_pids("implementer")
        assert DEAD_PID in pids, (
            "guard_pool_capacity should NOT remove dead PIDs — "
            "that's check_and_update_finished_agents' job"
        )

    def test_capacity_check_correct_with_dead_pids(self, runtime_dirs, monkeypatch):
        """guard_pool_capacity should report correct capacity despite dead PIDs."""
        agents_dir, tasks_dir = runtime_dirs

        save_blueprint_pids("implementer", {
            ALIVE_PID: {"task_id": "TASK-1", "started_at": "t", "instance_name": "imp-1"},
            DEAD_PID: {"task_id": "TASK-2", "started_at": "t", "instance_name": "imp-2"},
        })

        import orchestrator.scheduler as sched_mod

        with patch("orchestrator.pool.os.kill", side_effect=_fake_kill_dead_only):
            ctx = sched_mod.AgentContext(
                agent_name="implementer",
                agent_config={"blueprint_name": "implementer", "max_instances": 2},
                role="implement",
                interval=60,
                state={},
                state_path=Path("/dev/null"),
            )

            should_proceed, reason = sched_mod.guard_pool_capacity(ctx)

        # 1 alive out of max 2 → should proceed
        assert should_proceed is True

    def test_capacity_at_limit(self, runtime_dirs, monkeypatch):
        """guard_pool_capacity blocks when at max alive instances."""
        agents_dir, tasks_dir = runtime_dirs

        save_blueprint_pids("implementer", {
            ALIVE_PID: {"task_id": "TASK-1", "started_at": "t", "instance_name": "imp-1"},
            DEAD_PID: {"task_id": "TASK-2", "started_at": "t", "instance_name": "imp-2"},
        })

        import orchestrator.scheduler as sched_mod

        with patch("orchestrator.pool.os.kill", side_effect=_fake_kill_dead_only):
            ctx = sched_mod.AgentContext(
                agent_name="implementer",
                agent_config={"blueprint_name": "implementer", "max_instances": 1},
                role="implement",
                interval=60,
                state={},
                state_path=Path("/dev/null"),
            )

            should_proceed, reason = sched_mod.guard_pool_capacity(ctx)

        # 1 alive out of max 1 → at capacity
        assert should_proceed is False
        assert "at_capacity" in reason


# ---------------------------------------------------------------------------
# FIX VERIFICATION: _gather_agents must NOT call cleanup_dead_pids
# ---------------------------------------------------------------------------


class TestGatherAgentsNoCleanup:
    """After fix: _gather_agents should NOT call cleanup_dead_pids."""

    def test_gather_agents_does_not_call_cleanup(self, runtime_dirs, monkeypatch):
        """_gather_agents should use count_running_instances and
        get_active_task_ids instead of cleanup_dead_pids."""
        agents_dir, tasks_dir = runtime_dirs
        _setup_finished_agent(agents_dir, tasks_dir)

        monkeypatch.setattr(
            "orchestrator.config.get_agents",
            MagicMock(return_value=[{
                "name": "implementer",
                "blueprint_name": "implementer",
                "role": "implement",
                "max_instances": 1,
            }]),
        )
        monkeypatch.setattr(
            "orchestrator.config.get_notes_dir",
            MagicMock(return_value=agents_dir / "notes"),
        )

        # Track whether cleanup_dead_pids gets called
        cleanup_mock = MagicMock(return_value=0)
        monkeypatch.setattr("orchestrator.pool.cleanup_dead_pids", cleanup_mock)

        from orchestrator.reports import _gather_agents
        _gather_agents()

        cleanup_mock.assert_not_called()


# ---------------------------------------------------------------------------
# count_running_instances does NOT remove dead PIDs
# ---------------------------------------------------------------------------


class TestCountRunningIgnoresDeadPids:
    """count_running_instances skips dead PIDs without removing them."""

    def test_count_ignores_dead_without_removing(self, runtime_dirs):
        """count_running_instances should return correct count without removing dead PIDs."""
        agents_dir, tasks_dir = runtime_dirs

        save_blueprint_pids("implementer", {
            ALIVE_PID: {"task_id": "TASK-1", "started_at": "t", "instance_name": "imp-1"},
            DEAD_PID: {"task_id": "TASK-2", "started_at": "t", "instance_name": "imp-2"},
        })

        with patch("orchestrator.pool.os.kill", side_effect=_fake_kill_dead_only):
            count = count_running_instances("implementer")

        assert count == 1

        # Dead PID should still be in running_pids.json
        pids = load_blueprint_pids("implementer")
        assert DEAD_PID in pids, (
            "count_running_instances should NOT remove dead PIDs — "
            "that's check_and_update_finished_agents' job"
        )
        assert ALIVE_PID in pids
