"""Integration tests for orphaned agent recovery via PID tracking.

Tests that:
1. Dead PIDs detected by check_and_update_finished_agents → task moved to failed,
   PID removed from running_pids.json (real file I/O, not mocked)
2. Alive PIDs preserved by cleanup_dead_pids → task stays in claimed,
   PID stays in running_pids.json (real file I/O, not mocked)

Run with a local server on port 9787:
    cd submodules/server && npx wrangler dev --port 9787
"""

import os
import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.pool import (
    cleanup_dead_pids,
    load_blueprint_pids,
    register_instance_pid,
)
from orchestrator.scheduler import check_and_update_finished_agents


def _make_task_id() -> str:
    return f"PID-{uuid.uuid4().hex[:8].upper()}"


class TestDeadPIDRecovery:
    """Dead PID causes task to move to failed via check_and_update_finished_agents."""

    def test_dead_pid_detected_and_task_moved_to_failed(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Dead PID removed from running_pids.json and task moved to failed queue.

        Steps:
        1. Create and claim task (queue = claimed)
        2. Register a dead PID via register_instance_pid() — real file write
        3. Call check_and_update_finished_agents()
        4. Assert: dead PID removed from running_pids.json
        5. Assert: task now in failed queue
        """
        # 1. Create task and claim it
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title=f"PID recovery test {task_id}",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="pid-test-implementer",
            role_filter="implement",
        )

        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "claimed", (
            f"Expected task in 'claimed' after claim, got '{task['queue']}'"
        )

        # 2. Produce a dead PID: start a short-lived process and wait for it to exit
        proc = subprocess.Popen(["true"])
        dead_pid = proc.pid
        proc.wait()  # blocks until "true" exits — PID is now dead

        # 3. Set up temp dirs mirroring real agent/task layout
        agents_dir = tmp_path / "agents"
        tasks_dir = tmp_path / "tasks"
        blueprint_name = "implementer"

        # task_dir must exist — check_and_update_finished_agents checks task_dir.exists()
        # before processing result. No result.json → inferred as {"outcome": "error"}.
        task_dir = tasks_dir / task_id
        task_dir.mkdir(parents=True)

        # 4. Register dead PID using real pool functions (real running_pids.json)
        with patch("orchestrator.pool.get_agents_runtime_dir", return_value=agents_dir):
            register_instance_pid(blueprint_name, dead_pid, task_id, "implementer-test")

        # Confirm PID is in running_pids.json before check
        with patch("orchestrator.pool.get_agents_runtime_dir", return_value=agents_dir):
            pids_before = load_blueprint_pids(blueprint_name)
        assert dead_pid in pids_before, (
            f"Dead PID {dead_pid} should be registered before check"
        )

        # 5. Run check_and_update_finished_agents with patched paths
        #    - scheduler uses get_agents_runtime_dir() to scan agent dirs
        #    - pool uses get_agents_runtime_dir() inside load/save_blueprint_pids
        with (
            patch("orchestrator.scheduler.get_agents_runtime_dir", return_value=agents_dir),
            patch("orchestrator.pool.get_agents_runtime_dir", return_value=agents_dir),
            patch("orchestrator.scheduler.get_tasks_dir", return_value=tasks_dir),
            patch("orchestrator.scheduler.get_agents", return_value=[
                {"blueprint_name": blueprint_name, "claim_from": "incoming"}
            ]),
        ):
            check_and_update_finished_agents()

        # 6. Assert: dead PID removed from running_pids.json
        with patch("orchestrator.pool.get_agents_runtime_dir", return_value=agents_dir):
            pids_after = load_blueprint_pids(blueprint_name)
        assert dead_pid not in pids_after, (
            f"Dead PID {dead_pid} should have been removed from running_pids.json"
        )

        # 7. Assert: task moved to failed queue
        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "failed", (
            f"Expected task in 'failed' queue after dead PID recovery, got '{task['queue']}'"
        )


class TestAlivePIDPreservation:
    """Alive PIDs are not removed by cleanup_dead_pids."""

    def test_alive_pid_not_cleaned_up(
        self,
        scoped_sdk,
        orchestrator_id: str,
        tmp_path: Path,
        clean_tasks,
    ) -> None:
        """Alive PID stays in running_pids.json; task remains in claimed queue.

        Steps:
        1. Create and claim task (queue = claimed)
        2. Register current process PID (guaranteed alive) via register_instance_pid()
        3. Call cleanup_dead_pids()
        4. Assert: PID still in running_pids.json (0 removed)
        5. Assert: task still in claimed queue
        """
        # 1. Create task and claim it
        task_id = _make_task_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title=f"Alive PID test {task_id}",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="pid-test-implementer",
            role_filter="implement",
        )

        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "claimed", (
            f"Expected task in 'claimed' after claim, got '{task['queue']}'"
        )

        # 2. Register current process PID — it is alive for the duration of this test
        alive_pid = os.getpid()
        agents_dir = tmp_path / "agents"
        blueprint_name = "implementer"

        with patch("orchestrator.pool.get_agents_runtime_dir", return_value=agents_dir):
            register_instance_pid(blueprint_name, alive_pid, task_id, "implementer-test")

        # Confirm PID is registered
        with patch("orchestrator.pool.get_agents_runtime_dir", return_value=agents_dir):
            pids_before = load_blueprint_pids(blueprint_name)
        assert alive_pid in pids_before, (
            f"Alive PID {alive_pid} should be registered before cleanup"
        )

        # 3. Call cleanup_dead_pids — should leave the alive PID untouched
        with patch("orchestrator.pool.get_agents_runtime_dir", return_value=agents_dir):
            removed_count = cleanup_dead_pids(blueprint_name)

        # 4. Assert: alive PID still in running_pids.json
        assert removed_count == 0, (
            f"Expected 0 PIDs removed, got {removed_count}"
        )

        with patch("orchestrator.pool.get_agents_runtime_dir", return_value=agents_dir):
            pids_after = load_blueprint_pids(blueprint_name)
        assert alive_pid in pids_after, (
            f"Alive PID {alive_pid} should still be in running_pids.json after cleanup"
        )

        # 5. Assert: task still in claimed queue (cleanup_dead_pids is purely local — no API calls)
        task = scoped_sdk.tasks.get(task_id)
        assert task["queue"] == "claimed", (
            f"Expected task to remain in 'claimed' queue, got '{task['queue']}'"
        )
