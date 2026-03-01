"""Integration test: gatekeeper-approve lifecycle ends in done queue.

Regression test for the bug where _handle_approve_and_run_steps executed all
transition steps (merge PR, update changelog) but never called _perform_transition(),
leaving every approved task stuck in provisional/claimed instead of reaching done.

Run with a local test server on port 9787:
    ./tests/integration/bin/start-test-server.sh
"""

from pathlib import Path

from tests.integration.flow_helpers import create_provisional


class TestGatekeeperApproveLifecycle:
    """Gatekeeper approve: task in provisional → approve → done queue."""

    def _write_approved_stdout(self, task_dir: Path) -> None:
        """Write stdout.log that the mock fixture classifies as decision=approve."""
        task_dir.mkdir(parents=True, exist_ok=True)
        # mock_infer_result_from_stdout matches "approved" (not "rejected") → approve
        (task_dir / "stdout.log").write_text("DECISION: APPROVED\nThe implementation looks correct.")

    def test_approved_task_reaches_done_queue(
        self, scoped_sdk, orchestrator_id, tmp_path, monkeypatch
    ):
        """After gatekeeper approves, task must reach the done queue.

        Lifecycle: incoming → claimed → provisional → (gatekeeper) → done
        """
        # Advance to provisional
        task_id = create_provisional(scoped_sdk, orchestrator_id)

        # Claim from provisional (simulates gatekeeper claiming for review)
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-gatekeeper",
            queue="provisional",
        )
        assert claimed is not None, "Gatekeeper should be able to claim from provisional"
        assert claimed["id"] == task_id

        # Mock execute_steps to avoid actual git/PR operations
        monkeypatch.setattr("octopoid.steps.execute_steps", lambda *a, **kw: None)

        task_dir = tmp_path / task_id
        self._write_approved_stdout(task_dir)

        from octopoid.result_handler import handle_agent_result_via_flow
        result = handle_agent_result_via_flow(
            task_id, "test-gatekeeper", task_dir, expected_queue="provisional"
        )

        assert result is True
        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "done", (
            f"Approved gatekeeper task must reach 'done' queue, got {task['queue']!r}"
        )

    def test_approved_task_returns_true(
        self, scoped_sdk, orchestrator_id, tmp_path, monkeypatch
    ):
        """handle_agent_result_via_flow returns True (PID safe to remove) on approve."""
        task_id = create_provisional(scoped_sdk, orchestrator_id)

        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-gatekeeper",
            queue="provisional",
        )

        monkeypatch.setattr("octopoid.steps.execute_steps", lambda *a, **kw: None)

        task_dir = tmp_path / task_id
        self._write_approved_stdout(task_dir)

        from octopoid.result_handler import handle_agent_result_via_flow
        result = handle_agent_result_via_flow(
            task_id, "test-gatekeeper", task_dir, expected_queue="provisional"
        )

        assert result is True
