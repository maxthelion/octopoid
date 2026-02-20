"""Integration tests for duplicate claim prevention across pool instances.

Tests that guard_claim_task correctly prevents two pool instances from
working on the same task (dedup), and that different tasks can be claimed
by different instances without interference.

Requires a local test server running on port 9787.

Architecture under test
-----------------------
guard_claim_task (scheduler.py):
  1. Claims a task from the server via claim_and_prepare_task.
  2. Calls get_active_task_ids(blueprint_name) to check for alive PIDs
     already working on the claimed task.
  3. If a duplicate is detected: returns (False, "duplicate_task: ..."),
     leaving the task in 'claimed' state (NOT requeued — by design).
  4. If no duplicate: sets ctx.claimed_task and returns (True, "").
"""

import os
import uuid
from pathlib import Path

import pytest

from orchestrator.pool import (
    get_active_task_ids,
    get_blueprint_pids_path,
    register_instance_pid,
)
from orchestrator.scheduler import AgentContext, guard_claim_task
from orchestrator.state_utils import AgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blueprint() -> str:
    """Unique blueprint name per test — prevents cross-test PID file pollution."""
    return f"test-dedup-{uuid.uuid4().hex[:8]}"


def _make_task_id() -> str:
    """Unique task ID for each test."""
    return f"DEDUP-{uuid.uuid4().hex[:8].upper()}"


def _scripts_ctx(blueprint_name: str, state_path: Path) -> AgentContext:
    """Minimal scripts-mode AgentContext suitable for guard_claim_task."""
    return AgentContext(
        agent_config={
            "spawn_mode": "scripts",
            "blueprint_name": blueprint_name,
            "claim_from": "incoming",
        },
        agent_name=f"agent-{blueprint_name}",
        role="implement",
        interval=60,
        state=AgentState(),
        state_path=state_path,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDuplicateClaimPrevention:
    """guard_claim_task dedup logic with real server + real pool tracking."""

    def test_second_instance_blocked_on_duplicate_task(
        self,
        monkeypatch,
        sdk,
        orchestrator_id: str,
        clean_tasks,
        tmp_path: Path,
    ) -> None:
        """Instance-2 is blocked when instance-1 is already working on the task.

        Scenario: instance-1 has claimed a task and registered its PID.
        The task is still (or again) in the incoming queue (e.g. after a
        lease expiry or re-queue). When instance-2 calls guard_claim_task:

        1.  guard claims the task from the server (task → 'claimed').
        2.  guard calls get_active_task_ids(blueprint_name).
        3.  task_id is in active_task_ids because instance-1 (os.getpid(),
            which is alive) is registered for it.
        4.  guard returns (False, "duplicate_task: ...") without setting
            ctx.claimed_task — instance-2 does not proceed.
        """
        blueprint_name = _make_blueprint()
        task_id = _make_task_id()
        pids_path = get_blueprint_pids_path(blueprint_name)

        # Patch get_orchestrator_id so that claim_task uses the registered
        # test orchestrator ID (the prod ID from local config is not
        # registered on the test server and would cause a FK constraint error).
        monkeypatch.setattr("orchestrator.tasks.get_orchestrator_id", lambda: orchestrator_id)

        try:
            # 1. Create task in incoming.
            sdk.tasks.create(
                id=task_id,
                file_path=f".octopoid/tasks/{task_id}.md",
                title=f"Dedup prevention test {task_id}",
                role="implement",
                priority="P2",
                branch="main",
            )

            # 2. Register the current process as instance-1 already working on
            #    this task.  os.getpid() is guaranteed alive, so
            #    get_active_task_ids will include task_id.
            register_instance_pid(
                blueprint_name,
                os.getpid(),
                task_id,
                f"{blueprint_name}-1",
            )

            # Precondition: verify the task is tracked as active before the test.
            active_ids = get_active_task_ids(blueprint_name)
            assert task_id in active_ids, (
                f"Precondition failed: {task_id} must be in active_task_ids "
                f"before calling guard_claim_task. Got: {active_ids}"
            )

            # 3. Instance-2 calls guard_claim_task.
            #    Internally it claims the task from the server, then detects
            #    the duplicate and returns (False, reason).
            state_path = tmp_path / "state-instance2.json"
            ctx = _scripts_ctx(blueprint_name, state_path)

            proceed, reason = guard_claim_task(ctx)

            # 4. Assert dedup blocked instance-2.
            assert not proceed, (
                f"guard_claim_task should return False for a duplicate task, "
                f"got proceed={proceed!r}, reason={reason!r}"
            )
            assert "duplicate_task" in reason, (
                f"Expected 'duplicate_task' in reason string, got: {reason!r}"
            )
            # ctx.claimed_task must not be set — the guard blocked before
            # assigning the task to the context.
            assert ctx.claimed_task is None, (
                f"claimed_task should be None when the dedup guard fires, "
                f"got: {ctx.claimed_task}"
            )

        finally:
            # Remove the test blueprint's PID file so it does not persist
            # into subsequent test runs or affect the real scheduler.
            if pids_path.exists():
                pids_path.unlink()

    def test_different_tasks_claimed_by_different_instances(
        self,
        monkeypatch,
        sdk,
        orchestrator_id: str,
        clean_tasks,
        tmp_path: Path,
    ) -> None:
        """Two instances each claim a *different* task — no dedup fires.

        Instance-1 claims task-1 via the SDK and registers its PID.
        Instance-2 calls guard_claim_task; only task-2 is still in incoming
        (task-1 is already claimed), so it claims task-2.  Because task-2 is
        not in get_active_task_ids for this blueprint, the guard proceeds and
        sets ctx.claimed_task to task-2.

        Both instances end up working on distinct tasks.
        """
        # Same FK constraint fix — claim_task needs the registered test orchestrator.
        monkeypatch.setattr("orchestrator.tasks.get_orchestrator_id", lambda: orchestrator_id)

        blueprint_name = _make_blueprint()
        task_id_1 = _make_task_id()
        task_id_2 = _make_task_id()
        pids_path = get_blueprint_pids_path(blueprint_name)

        try:
            # 1. Create both tasks in incoming.
            sdk.tasks.create(
                id=task_id_1,
                file_path=f".octopoid/tasks/{task_id_1}.md",
                title=f"Multi-instance task 1 {task_id_1}",
                role="implement",
                priority="P2",
                branch="main",
            )
            sdk.tasks.create(
                id=task_id_2,
                file_path=f".octopoid/tasks/{task_id_2}.md",
                title=f"Multi-instance task 2 {task_id_2}",
                role="implement",
                priority="P2",
                branch="main",
            )

            # 2. Instance-1 claims task-1 directly via the SDK and registers
            #    its PID (simulating a running spawn).
            claimed_1 = sdk.tasks.claim(
                orchestrator_id=orchestrator_id,
                agent_name=f"agent-{blueprint_name}-1",
                role_filter="implement",
            )
            assert claimed_1 is not None, (
                "Instance-1 should successfully claim a task from incoming"
            )
            register_instance_pid(
                blueprint_name,
                os.getpid(),
                claimed_1["id"],
                f"{blueprint_name}-1",
            )

            # 3. Instance-2 calls guard_claim_task.
            #    task-1 is already claimed (not in incoming), so the only
            #    available task is task-2.  task-2 is not in active_task_ids,
            #    so no dedup fires.
            state_path = tmp_path / "state-instance2.json"
            ctx = _scripts_ctx(blueprint_name, state_path)

            proceed, reason = guard_claim_task(ctx)

            # 4. Assert: instance-2 succeeded with a different task.
            assert proceed, (
                f"guard_claim_task should succeed for a different (non-duplicate) "
                f"task, got proceed={proceed!r}, reason={reason!r}"
            )
            assert ctx.claimed_task is not None, (
                "Instance-2 should have a claimed task after guard_claim_task succeeds"
            )
            assert ctx.claimed_task["id"] != claimed_1["id"], (
                f"Instances should be working on different tasks: "
                f"instance-1 has {claimed_1['id']!r}, "
                f"instance-2 has {ctx.claimed_task['id']!r}"
            )

            # Both task IDs should be trackable as active.
            active_ids = get_active_task_ids(blueprint_name)
            assert claimed_1["id"] in active_ids, (
                f"task-1 ({claimed_1['id']}) should be in active_task_ids"
            )

        finally:
            if pids_path.exists():
                pids_path.unlink()
