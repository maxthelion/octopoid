"""Integration tests for scheduler lifecycle: happy path, failure, crash, rejection.

These tests exercise handle_agent_result, handle_agent_result_via_flow, and
check_and_update_finished_agents end-to-end with:
- A real local test server (localhost:9787) for API validation
- mock-agent.sh for deterministic agent behavior
- mock gh CLI for deterministic GitHub operations
- A local bare git repo (test_repo fixture) for push/PR operations

IMPORTANT: All tests run against localhost:9787, never production.
"""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.scheduler import (
    check_and_update_finished_agents,
    handle_agent_result,
    handle_agent_result_via_flow,
)
from orchestrator.state_utils import AgentState, mark_started, save_state

# Path to mock fixtures (mock-agent.sh, bin/gh)
_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
_MOCK_BIN = _FIXTURES_DIR / "bin"


def _with_mock_gh(original_path: str) -> str:
    """Return a PATH string with mock-gh bin prepended."""
    return f"{_MOCK_BIN}:{original_path}"


def _setup_worktree_clone(task_dir: Path, remote: Path) -> Path:
    """Clone a bare remote into task_dir/worktree and put it in detached HEAD.

    Detached HEAD is required so that ensure_on_branch() can create the
    task branch (it refuses to create a branch when already on a named branch).

    Returns the worktree path.
    """
    worktree = task_dir / "worktree"
    subprocess.run(
        ["git", "clone", str(remote), str(worktree)],
        check=True, capture_output=True,
    )
    # Configure git identity in the clone (needed for commits by mock-agent)
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"],
        cwd=worktree, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=worktree, check=True, capture_output=True,
    )
    # Detach HEAD so the push_branch step can create the task branch cleanly
    subprocess.run(
        ["git", "checkout", "--detach", "HEAD"],
        cwd=worktree, check=True, capture_output=True,
    )
    return worktree


class TestAgentFailure:
    """Agent writes outcome=failure → task goes to failed queue."""

    def test_agent_failure(self, sdk, orchestrator_id, clean_tasks, tmp_path, run_mock_agent):
        task_id = "lifecycle-fail-001"
        sdk.tasks.create(
            id=task_id,
            file_path=f"/tmp/{task_id}.md",
            title="Failure Test",
            role="implement",
            branch="main",
        )
        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )

        task_dir = tmp_path / task_id
        task_dir.mkdir()
        (task_dir / "worktree").mkdir()

        proc = run_mock_agent(
            task_dir,
            agent_env={"MOCK_OUTCOME": "failure", "MOCK_REASON": "tests failed"},
        )
        assert proc.returncode == 0, f"mock-agent failed: {proc.stderr}"

        handle_agent_result(task_id, "test-agent", task_dir)

        task = sdk.tasks.get(task_id)
        assert task["queue"] == "failed", f"Expected failed, got {task['queue']}"


class TestAgentCrash:
    """Agent exits without result.json → task moves out of claimed (not orphaned)."""

    def test_agent_crash_no_result(self, sdk, orchestrator_id, clean_tasks, tmp_path, run_mock_agent):
        task_id = "lifecycle-crash-001"
        sdk.tasks.create(
            id=task_id,
            file_path=f"/tmp/{task_id}.md",
            title="Crash Test",
            role="implement",
            branch="main",
        )
        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )

        task_dir = tmp_path / task_id
        task_dir.mkdir()
        (task_dir / "worktree").mkdir()

        # MOCK_CRASH=true: agent exits 1 without writing result.json
        proc = run_mock_agent(task_dir, agent_env={"MOCK_CRASH": "true"})
        assert proc.returncode != 0, "mock-agent with MOCK_CRASH=true should exit non-zero"
        assert not (task_dir / "result.json").exists(), "result.json must not exist after crash"

        handle_agent_result(task_id, "test-agent", task_dir)

        task = sdk.tasks.get(task_id)
        # Key assertion: task must NOT be orphaned in claimed
        # (current behavior: crash with no result.json → outcome=error → failed queue)
        assert task["queue"] != "claimed", (
            f"Task stuck in claimed after agent crash: {task}"
        )
        assert task["queue"] == "failed", (
            f"Expected failed after crash (no result.json), got {task['queue']}"
        )

    def test_check_and_update_detects_crashed_agent(
        self, sdk, orchestrator_id, clean_tasks, tmp_path, run_mock_agent
    ):
        """check_and_update_finished_agents detects a crashed agent and handles the task."""
        import tempfile

        task_id = "lifecycle-crash-detect-001"
        sdk.tasks.create(
            id=task_id,
            file_path=f"/tmp/{task_id}.md",
            title="Crash Detect Test",
            role="implement",
            branch="main",
        )
        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="crash-agent",
            role_filter="implement",
        )

        # Set up a task dir with no result.json (simulating crash)
        task_dir = tmp_path / task_id
        task_dir.mkdir()
        (task_dir / "worktree").mkdir()

        # Use a temp dir for ORCHESTRATOR_DIR so we don't pollute the real agents dir
        with tempfile.TemporaryDirectory() as orch_dir_str:
            orch_dir = Path(orch_dir_str)
            agent_dir = orch_dir / "runtime" / "agents" / "crash-agent"
            agent_dir.mkdir(parents=True)

            # Write agent state: running=True, dead PID (99999 — almost certainly not running)
            state = AgentState(
                running=True,
                pid=99999,  # almost certainly not running
                current_task=task_id,
                extra={
                    "agent_mode": "scripts",
                    "task_dir": str(task_dir),
                    "current_task_id": task_id,
                    "claim_from": "incoming",
                },
            )
            state_path = agent_dir / "state.json"
            save_state(state, state_path)

            # Patch ORCHESTRATOR_DIR so get_agents_runtime_dir() returns our temp dir
            original_orch_dir = os.environ.get("ORCHESTRATOR_DIR")
            os.environ["ORCHESTRATOR_DIR"] = str(orch_dir)
            try:
                # Clear cached SDK so get_sdk() uses the test server
                import orchestrator.sdk as sdk_module
                sdk_module._sdk = None

                check_and_update_finished_agents()
            finally:
                if original_orch_dir is None:
                    os.environ.pop("ORCHESTRATOR_DIR", None)
                else:
                    os.environ["ORCHESTRATOR_DIR"] = original_orch_dir
                # Force re-clear SDK cache after env restore
                sdk_module._sdk = None

        task = sdk.tasks.get(task_id)
        # Task must not be orphaned in claimed after agent crash
        assert task["queue"] != "claimed", (
            f"Task stuck in claimed after check_and_update_finished_agents: {task}"
        )


class TestNeedsContinuation:
    """Agent writes outcome=needs_continuation → task goes to needs_continuation queue."""

    def test_needs_continuation(
        self, sdk, orchestrator_id, clean_tasks, tmp_path, run_mock_agent
    ):
        task_id = "lifecycle-cont-001"
        sdk.tasks.create(
            id=task_id,
            file_path=f"/tmp/{task_id}.md",
            title="Continuation Test",
            role="implement",
            branch="main",
        )
        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )

        task_dir = tmp_path / task_id
        task_dir.mkdir()
        (task_dir / "worktree").mkdir()

        proc = run_mock_agent(task_dir, agent_env={"MOCK_OUTCOME": "needs_continuation"})
        assert proc.returncode == 0, f"mock-agent failed: {proc.stderr}"

        handle_agent_result(task_id, "test-agent", task_dir)

        task = sdk.tasks.get(task_id)
        assert task["queue"] == "needs_continuation", (
            f"Expected needs_continuation, got {task['queue']}"
        )


class TestGatekeeperRejects:
    """Gatekeeper writes decision=reject → task returns to incoming with feedback."""

    def test_gatekeeper_rejects(
        self, sdk, orchestrator_id, clean_tasks, tmp_path, run_mock_agent
    ):
        task_id = "lifecycle-reject-001"
        sdk.tasks.create(
            id=task_id,
            file_path=f"/tmp/{task_id}.md",
            title="Gatekeeper Reject Test",
            role="implement",
            branch="main",
        )
        # Advance task to provisional (implementer done, ready for gatekeeper review)
        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="implementer-1",
            role_filter="implement",
        )
        sdk.tasks.submit(task_id, commits_count=1, turns_used=5)

        task = sdk.tasks.get(task_id)
        assert task["queue"] == "provisional", (
            f"Expected provisional before gatekeeper, got {task['queue']}"
        )

        # Gatekeeper produces a reject result
        gate_dir = tmp_path / "gate-dir"
        gate_dir.mkdir()
        (gate_dir / "worktree").mkdir()

        proc = run_mock_agent(
            gate_dir,
            agent_env={
                "MOCK_OUTCOME": "success",
                "MOCK_DECISION": "reject",
                "MOCK_COMMENT": "Missing tests — please add coverage",
            },
        )
        assert proc.returncode == 0, f"mock-agent (gatekeeper) failed: {proc.stderr}"

        handle_agent_result_via_flow(task_id, "gatekeeper-1", gate_dir)

        task = sdk.tasks.get(task_id)
        assert task["queue"] == "incoming", (
            f"Expected task back in incoming after gatekeeper reject, got {task['queue']}"
        )


class TestHappyPathLifecycle:
    """Full lifecycle: incoming → claimed → agent succeeds → provisional → gatekeeper → done."""

    def test_happy_path_lifecycle(
        self, sdk, orchestrator_id, clean_tasks, tmp_path, test_repo, run_mock_agent
    ):
        """Complete lifecycle test using mock agent and mock gh."""
        original_path = os.environ.get("PATH", "")
        mock_path = _with_mock_gh(original_path)

        task_id = "lifecycle-happy-001"
        sdk.tasks.create(
            id=task_id,
            file_path=f"/tmp/{task_id}.md",
            title="Happy Path Test",
            role="implement",
            branch="main",
        )

        # ── Phase 1: Implementer claims and succeeds ──────────────────────────

        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="implementer-1",
            role_filter="implement",
        )
        task = sdk.tasks.get(task_id)
        assert task["queue"] == "claimed", f"Expected claimed, got {task['queue']}"

        # Set up task dir with a real git worktree (for push_branch step)
        task_dir = tmp_path / task_id
        task_dir.mkdir()
        _setup_worktree_clone(task_dir, test_repo["remote"])

        # Run mock agent — makes 1 commit and writes success result.json
        proc = run_mock_agent(
            task_dir,
            agent_env={"MOCK_OUTCOME": "success", "MOCK_COMMITS": "1"},
            gh_env={"GH_MOCK_PR_NUMBER": "42"},
        )
        assert proc.returncode == 0, (
            f"mock-agent (implementer) failed:\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )

        result = json.loads((task_dir / "result.json").read_text())
        assert result["outcome"] == "done"

        # Call handle_agent_result with mock-gh in PATH.
        # The flow steps run: push_branch, run_tests (no-op, no test runner),
        # create_pr (mock-gh), submit_to_server (real test API).
        os.environ["PATH"] = mock_path
        try:
            # Clear cached SDK so get_sdk() uses OCTOPOID_SERVER_URL (test server)
            import orchestrator.sdk as sdk_module
            sdk_module._sdk = None
            handle_agent_result(task_id, "implementer-1", task_dir)
        finally:
            os.environ["PATH"] = original_path
            sdk_module._sdk = None

        task = sdk.tasks.get(task_id)
        assert task["queue"] == "provisional", (
            f"Expected provisional after implementer success, got {task['queue']}"
        )

        # ── Phase 2: Gatekeeper reviews and approves ──────────────────────────

        gate_dir = tmp_path / f"{task_id}-gate"
        gate_dir.mkdir()
        (gate_dir / "worktree").mkdir()

        # Gatekeeper produces an approve result
        proc = run_mock_agent(
            gate_dir,
            agent_env={
                "MOCK_OUTCOME": "success",
                "MOCK_DECISION": "approve",
                "MOCK_COMMENT": "Looks good!",
            },
            gh_env={"GH_MOCK_PR_NUMBER": "42"},
        )
        assert proc.returncode == 0, (
            f"mock-agent (gatekeeper) failed:\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )

        gate_result = json.loads((gate_dir / "result.json").read_text())
        assert gate_result["decision"] == "approve"

        # Call handle_agent_result_via_flow with mock-gh in PATH.
        # The flow steps run: post_review_comment (mock-gh), merge_pr (mock-gh + accept).
        # Note: task.pr_number is set from Phase 1's create_pr step.
        os.environ["PATH"] = mock_path
        try:
            sdk_module._sdk = None
            handle_agent_result_via_flow(task_id, "gatekeeper-1", gate_dir)
        finally:
            os.environ["PATH"] = original_path
            sdk_module._sdk = None

        task = sdk.tasks.get(task_id)
        assert task["queue"] == "done", (
            f"Expected done after gatekeeper approval, got {task['queue']}"
        )
