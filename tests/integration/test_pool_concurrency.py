"""Integration tests for pool capacity limits with mock agents.

These are unit-style tests (no server needed) — pool tracking is filesystem-based.
Exercises orchestrator/pool.py and the guard_pool_capacity guard in scheduler.py.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.pool import (
    count_running_instances,
    load_blueprint_pids,
    register_instance_pid,
    save_blueprint_pids,
)
from orchestrator.scheduler import AgentContext, guard_pool_capacity
from orchestrator.state_utils import AgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_context(blueprint_name: str, max_instances: int) -> AgentContext:
    """Create a minimal AgentContext for pool capacity testing."""
    state = AgentState()
    return AgentContext(
        agent_config={
            "blueprint_name": blueprint_name,
            "max_instances": max_instances,
        },
        agent_name=blueprint_name,
        role="implementer",
        interval=60,
        state=state,
        state_path=Path("/tmp/fake-state.json"),
    )


def _find_dead_pid() -> int:
    """Return a PID that is guaranteed not to be running."""
    for candidate in range(999900, 1000000):
        try:
            os.kill(candidate, 0)
        except (OSError, ProcessLookupError):
            return candidate
    raise RuntimeError("Could not find a dead PID for testing")


# ---------------------------------------------------------------------------
# Test 1: Pool capacity respected
# ---------------------------------------------------------------------------


class TestPoolCapacityRespected:
    """guard_pool_capacity blocks at max capacity and allows when a slot frees up."""

    def test_capacity_respected(self, tmp_path: Path) -> None:
        """guard_pool_capacity returns False at capacity, True after a slot frees."""
        blueprint = "test-blueprint"

        with patch("orchestrator.pool.get_agents_runtime_dir", return_value=tmp_path):
            # Register 2 alive PIDs (current process and its parent are guaranteed alive)
            alive_pid1 = os.getpid()
            alive_pid2 = os.getppid()
            register_instance_pid(blueprint, alive_pid1, "TASK-1", f"{blueprint}-1")
            register_instance_pid(blueprint, alive_pid2, "TASK-2", f"{blueprint}-2")

            ctx = _make_agent_context(blueprint, max_instances=2)

            # At capacity: guard should block
            should_proceed, reason = guard_pool_capacity(ctx)
            assert not should_proceed
            assert "at_capacity" in reason
            assert "2/2" in reason

            # Simulate one agent finishing by removing one PID
            pids = load_blueprint_pids(blueprint)
            del pids[alive_pid2]
            save_blueprint_pids(blueprint, pids)

            # One slot free: guard should allow
            should_proceed, reason = guard_pool_capacity(ctx)
            assert should_proceed
            assert reason == ""


# ---------------------------------------------------------------------------
# Test 2: Count only alive PIDs
# ---------------------------------------------------------------------------


class TestCountOnlyAlivePids:
    """count_running_instances ignores dead PIDs."""

    def test_count_only_alive_pids(self, tmp_path: Path) -> None:
        """count_running_instances returns 2 when 3 PIDs registered but 1 is dead."""
        blueprint = "test-blueprint-alive"
        dead_pid = _find_dead_pid()

        with patch("orchestrator.pool.get_agents_runtime_dir", return_value=tmp_path):
            alive_pid1 = os.getpid()
            alive_pid2 = os.getppid()
            register_instance_pid(blueprint, alive_pid1, "TASK-A", f"{blueprint}-1")
            register_instance_pid(blueprint, alive_pid2, "TASK-B", f"{blueprint}-2")
            register_instance_pid(blueprint, dead_pid, "TASK-C", f"{blueprint}-3")

            count = count_running_instances(blueprint)
            assert count == 2
