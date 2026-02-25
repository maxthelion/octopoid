"""Integration tests for stale PID cleanup when task is already in terminal state.

The bug: when a task has been moved to done/failed (by lease expiry, manual
intervention, or a 409 race) before handle_agent_result processes the result,
the handler returned False — keeping the dead PID in running_pids.json forever
and blocking guard_pool_capacity from spawning new agents.

The fix: handlers return True for terminal-state tasks so
check_and_update_finished_agents removes the stale PID from the pool.

Run with a local server on port 9787:
    cd submodules/server && npx wrangler dev --port 9787
"""

import json
import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.pool import (
    load_blueprint_pids,
    register_instance_pid,
)
from orchestrator.scheduler import check_and_update_finished_agents, handle_agent_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task_id() -> str:
    return f"STP-{uuid.uuid4().hex[:8].upper()}"


def _create_and_claim(scoped_sdk, orchestrator_id: str, task_id: str) -> None:
    """Create a task and claim it (queue → claimed)."""
    scoped_sdk.tasks.create(
        id=task_id,
        file_path=f".octopoid/tasks/{task_id}.md",
        title=f"Stale PID terminal state test {task_id}",
        role="implement",
        branch="main",
    )
    scoped_sdk.tasks.claim(
        orchestrator_id=orchestrator_id,
        agent_name="test-implementer",
        role_filter="implement",
    )


# ---------------------------------------------------------------------------
# Test: handler returns True when task is already in done queue
# ---------------------------------------------------------------------------


class TestHandlerReturnsTrueForDoneQueue:
    """handle_agent_result() returns True (PID safe to remove) when task is in done."""

    def test_handler_returns_true_when_task_in_done_queue(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """handle_agent_result returns True for a task already in done.

        Steps:
        1. Create and claim task (queue = claimed)
        2. Advance task to done via claim → submit → accept
        3. Write result.json with outcome=done
        4. Call handle_agent_result()
        5. Assert return value is True (stale PID safe to remove)
        6. Assert task remains in done (not corrupted)
        """
        task_id = _make_task_id()
        _create_and_claim(scoped_sdk, orchestrator_id, task_id)

        # Advance to done: claim → submit → accept
        scoped_sdk.tasks.submit(task_id, commits_count=1, turns_used=1)
        scoped_sdk.tasks.accept(task_id, accepted_by="test-setup")

        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "done", f"Expected done before handler call, got {task['queue']}"

        # Write a result.json in a fresh task_dir
        task_dir = tmp_path / "task"
        task_dir.mkdir(parents=True)
        (task_dir / "result.json").write_text(json.dumps({"outcome": "done"}))

        # Call handler — must return True (stale PID safe to remove)
        result = handle_agent_result(task_id, "test-implementer", task_dir)

        assert result is True, (
            f"Expected handle_agent_result to return True for task in done queue, got {result}"
        )

        # Task must remain in done — handler must not corrupt the state
        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "done", (
            f"Expected task to remain in done after stale-PID handling, got {task['queue']}"
        )


# ---------------------------------------------------------------------------
# Test: handler returns True when task is already in failed queue
# ---------------------------------------------------------------------------


class TestHandlerReturnsTrueForFailedQueue:
    """handle_agent_result() returns True (PID safe to remove) when task is in failed."""

    def test_handler_returns_true_when_task_in_failed_queue(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """handle_agent_result returns True for a task already in failed.

        Steps:
        1. Create and claim task (queue = claimed)
        2. Move task to failed via direct update (simulates lease expiry / manual intervention)
        3. Write result.json with outcome=done
        4. Call handle_agent_result()
        5. Assert return value is True (stale PID safe to remove)
        6. Assert task remains in failed (not corrupted)
        """
        task_id = _make_task_id()
        _create_and_claim(scoped_sdk, orchestrator_id, task_id)

        # Move to failed, simulating lease expiry or manual intervention
        scoped_sdk.tasks.update(task_id, queue="failed")

        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "failed", f"Expected failed before handler call, got {task['queue']}"

        task_dir = tmp_path / "task"
        task_dir.mkdir(parents=True)
        (task_dir / "result.json").write_text(json.dumps({"outcome": "done"}))

        result = handle_agent_result(task_id, "test-implementer", task_dir)

        assert result is True, (
            f"Expected handle_agent_result to return True for task in failed queue, got {result}"
        )

        # Task must remain in failed
        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "failed", (
            f"Expected task to remain in failed after stale-PID handling, got {task['queue']}"
        )

    def test_handler_returns_true_for_failed_outcome_on_failed_task(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """handle_agent_result returns True when outcome=failed AND task is in failed.

        Covers the _handle_fail_outcome path (outcome=failed, queue=failed).
        """
        task_id = _make_task_id()
        _create_and_claim(scoped_sdk, orchestrator_id, task_id)

        scoped_sdk.tasks.update(task_id, queue="failed")

        task_dir = tmp_path / "task"
        task_dir.mkdir(parents=True)
        (task_dir / "result.json").write_text(json.dumps({"outcome": "failed", "reason": "already failed"}))

        result = handle_agent_result(task_id, "test-implementer", task_dir)

        assert result is True, (
            f"Expected handle_agent_result to return True (outcome=failed, queue=failed), got {result}"
        )


# ---------------------------------------------------------------------------
# Test: handler still returns False when task is in provisional queue
# ---------------------------------------------------------------------------


class TestHandlerReturnsFalseForProvisionalQueue:
    """handle_agent_result() returns False (keep PID for retry) for provisional."""

    def test_handler_returns_false_when_task_in_provisional_queue(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """handle_agent_result returns False for a task in provisional (transient).

        Provisional is not terminal — the task may still be transitioned.
        The PID must be kept so the scheduler retries next tick.

        Steps:
        1. Create and claim task (queue = claimed)
        2. Submit to provisional
        3. Write result.json with outcome=done
        4. Call handle_agent_result()
        5. Assert return value is False (keep PID for retry)
        """
        task_id = _make_task_id()
        _create_and_claim(scoped_sdk, orchestrator_id, task_id)

        # Advance to provisional
        scoped_sdk.tasks.submit(task_id, commits_count=1, turns_used=1)

        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "provisional", f"Expected provisional, got {task['queue']}"

        task_dir = tmp_path / "task"
        task_dir.mkdir(parents=True)
        (task_dir / "result.json").write_text(json.dumps({"outcome": "done"}))

        result = handle_agent_result(task_id, "test-implementer", task_dir)

        assert result is False, (
            f"Expected handle_agent_result to return False for task in provisional queue "
            f"(transient state, keep PID for retry), got {result}"
        )

        # Task must remain in provisional — handler must not corrupt the state
        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "provisional", (
            f"Expected task to remain in provisional after skipped handling, got {task['queue']}"
        )


# ---------------------------------------------------------------------------
# Test: PID cleanup — stale PID removed from pool for terminal-state task
# ---------------------------------------------------------------------------


class TestStalePidCleanupForTerminalTask:
    """Stale PID is removed from running_pids.json when task is in terminal state."""

    def test_stale_pid_removed_for_done_task(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Stale PID is removed from running_pids.json when task is already done.

        This is the core regression test for the original bug:
        3 stale PIDs blocked the pool because they were never removed.

        Steps:
        1. Create and claim task
        2. Advance task to done
        3. Produce a dead PID via a short-lived subprocess
        4. Register dead PID in running_pids.json
        5. Write result.json with outcome=done
        6. Call check_and_update_finished_agents()
        7. Assert: dead PID removed from running_pids.json
        8. Assert: task remains in done (not corrupted)
        """
        task_id = _make_task_id()
        _create_and_claim(scoped_sdk, orchestrator_id, task_id)

        # Advance to done
        scoped_sdk.tasks.submit(task_id, commits_count=1, turns_used=1)
        scoped_sdk.tasks.accept(task_id, accepted_by="test-setup")

        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "done", f"Expected done, got {task['queue']}"

        # Produce a dead PID
        proc = subprocess.Popen(["true"])
        dead_pid = proc.pid
        proc.wait()  # blocks until "true" exits — PID is now dead

        # Set up fake runtime directories mirroring real agent layout
        agents_dir = tmp_path / "agents"
        tasks_dir = tmp_path / "tasks"
        blueprint_name = "implementer"

        # Write result.json so check_and_update_finished_agents finds it
        task_dir = tasks_dir / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "result.json").write_text(json.dumps({"outcome": "done"}))

        # Register dead PID
        with patch("orchestrator.pool.get_agents_runtime_dir", return_value=agents_dir):
            register_instance_pid(blueprint_name, dead_pid, task_id, "implementer-test")

        with patch("orchestrator.pool.get_agents_runtime_dir", return_value=agents_dir):
            pids_before = load_blueprint_pids(blueprint_name)
        assert dead_pid in pids_before, f"Dead PID {dead_pid} should be registered before check"

        # Run check_and_update_finished_agents — must remove the stale PID
        with (
            patch("orchestrator.scheduler.get_agents_runtime_dir", return_value=agents_dir),
            patch("orchestrator.pool.get_agents_runtime_dir", return_value=agents_dir),
            patch("orchestrator.scheduler.get_tasks_dir", return_value=tasks_dir),
            patch("orchestrator.scheduler.get_agents", return_value=[
                {"blueprint_name": blueprint_name, "claim_from": "incoming"}
            ]),
        ):
            check_and_update_finished_agents()

        # Assert: stale PID removed
        with patch("orchestrator.pool.get_agents_runtime_dir", return_value=agents_dir):
            pids_after = load_blueprint_pids(blueprint_name)
        assert dead_pid not in pids_after, (
            f"Stale PID {dead_pid} should have been removed from running_pids.json "
            f"because task {task_id} was already in 'done' queue"
        )

        # Assert: task remains in done (not corrupted by the handler)
        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "done", (
            f"Expected task to remain in done after stale PID cleanup, got {task['queue']}"
        )
