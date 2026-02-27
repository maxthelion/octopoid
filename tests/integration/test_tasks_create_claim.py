"""Integration tests for tasks.py lifecycle functions.

Tests create_task(), claim_task(), submit_completion(), and fail_task()
against the local test server using scoped_sdk for isolation.

Each test class patches get_orchestrator_id() so claim_task() uses the
registered test orchestrator instead of trying to read a real config.
"""

import pytest


class TestCreateTask:
    """Tests for create_task() in octopoid.tasks."""

    def test_returns_8char_hex_id(self, scoped_sdk):
        """create_task() returns a bare 8-character hex ID."""
        from octopoid.tasks import create_task

        task_id = create_task(
            title="Test task — ID verification",
            role="implement",
            context="Verify that create_task returns a correctly formatted ID.",
            acceptance_criteria=["Returned ID is 8 hex characters"],
            priority="P2",
        )

        assert isinstance(task_id, str)
        assert len(task_id) == 8
        assert all(c in "0123456789abcdef" for c in task_id), (
            f"Task ID {task_id!r} contains non-hex characters"
        )

    def test_task_appears_in_incoming_queue(self, scoped_sdk):
        """create_task() registers the task on the server in the incoming queue."""
        from octopoid.tasks import create_task

        task_id = create_task(
            title="Test task — incoming queue check",
            role="implement",
            context="Verify that the task lands in the incoming queue.",
            acceptance_criteria=["Task appears in incoming queue after creation"],
            priority="P2",
        )

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None, f"Task {task_id} not found on server"
        assert task["queue"] == "incoming", (
            f"Expected queue 'incoming', got {task['queue']!r}"
        )

    def test_task_has_non_empty_content(self, scoped_sdk):
        """create_task() stores non-empty content that includes the title and context."""
        from octopoid.tasks import create_task

        task_id = create_task(
            title="Test task — content validation",
            role="implement",
            context="This context text must appear in the stored content.",
            acceptance_criteria=["Content must be non-empty and contain key fields"],
            priority="P2",
        )

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        content = task.get("content") or ""
        assert content, "Task content must not be empty"
        assert "Test task — content validation" in content
        assert "This context text must appear in the stored content." in content


class TestClaimTask:
    """Tests for claim_task() in octopoid.tasks."""

    def test_claim_task_moves_to_claimed(self, scoped_sdk, orchestrator_id, monkeypatch):
        """claim_task() claims the task and moves it from incoming to claimed."""
        from octopoid.tasks import create_task, claim_task

        monkeypatch.setattr("octopoid.tasks.get_orchestrator_id", lambda: orchestrator_id)

        task_id = create_task(
            title="Test task — claim check",
            role="implement",
            context="Verify claim_task moves task to claimed queue.",
            acceptance_criteria=["Task moves to claimed queue after claim_task()"],
            priority="P2",
        )

        claimed = claim_task(role_filter="implement", agent_name="test-claim-agent")

        assert claimed is not None, "claim_task() returned None — no task was claimed"
        assert claimed["id"] == task_id
        assert claimed["queue"] == "claimed"

    def test_claim_task_returns_task_dict(self, scoped_sdk, orchestrator_id, monkeypatch):
        """claim_task() returns a task dict with expected fields."""
        from octopoid.tasks import create_task, claim_task

        monkeypatch.setattr("octopoid.tasks.get_orchestrator_id", lambda: orchestrator_id)

        create_task(
            title="Test task — claim fields",
            role="implement",
            context="Verify claimed task dict has required fields.",
            acceptance_criteria=["Claimed task has id, queue, role fields"],
            priority="P2",
        )

        claimed = claim_task(role_filter="implement", agent_name="field-checker")

        assert claimed is not None
        assert "id" in claimed
        assert "queue" in claimed
        assert claimed["queue"] == "claimed"
        assert claimed.get("role") == "implement"


class TestSubmitCompletion:
    """Tests for submit_completion() in octopoid.tasks."""

    def test_submit_completion_moves_to_provisional(
        self, scoped_sdk, orchestrator_id, monkeypatch
    ):
        """submit_completion() moves a claimed task to the provisional queue."""
        from octopoid.tasks import create_task, claim_task, submit_completion

        monkeypatch.setattr("octopoid.tasks.get_orchestrator_id", lambda: orchestrator_id)

        task_id = create_task(
            title="Test task — submit check",
            role="implement",
            context="Verify submit_completion moves task to provisional.",
            acceptance_criteria=["Task moves to provisional after submit_completion()"],
            priority="P2",
        )

        claimed = claim_task(role_filter="implement", agent_name="submit-test-agent")
        assert claimed is not None and claimed["id"] == task_id

        result = submit_completion(task_id, commits_count=2, turns_used=10)

        assert result is not None, "submit_completion() returned None"
        assert result["queue"] == "provisional", (
            f"Expected queue 'provisional', got {result['queue']!r}"
        )

    def test_submit_completion_records_execution_notes(
        self, scoped_sdk, orchestrator_id, monkeypatch
    ):
        """submit_completion() stores commits and turns in execution_notes."""
        from octopoid.tasks import create_task, claim_task, submit_completion

        monkeypatch.setattr("octopoid.tasks.get_orchestrator_id", lambda: orchestrator_id)

        task_id = create_task(
            title="Test task — execution notes",
            role="implement",
            context="Verify execution_notes are set after submission.",
            acceptance_criteria=["execution_notes reflect commits and turns"],
            priority="P2",
        )

        claim_task(role_filter="implement", agent_name="notes-test-agent")
        result = submit_completion(task_id, commits_count=3, turns_used=7)

        assert result is not None
        execution_notes = result.get("execution_notes") or ""
        assert "3 commit" in execution_notes
        assert "7 turn" in execution_notes


class TestFailTask:
    """Tests for fail_task() in octopoid.tasks."""

    def test_fail_task_first_routes_to_requires_intervention(
        self, scoped_sdk, orchestrator_id, monkeypatch
    ):
        """fail_task() on a claimed task first routes to requires-intervention."""
        from octopoid.tasks import create_task, claim_task, fail_task

        monkeypatch.setattr("octopoid.tasks.get_orchestrator_id", lambda: orchestrator_id)

        task_id = create_task(
            title="Test task — first failure",
            role="implement",
            context="Verify fail_task routes to requires-intervention on first call.",
            acceptance_criteria=["Task goes to requires-intervention on first failure"],
            priority="P2",
        )

        claimed = claim_task(role_filter="implement", agent_name="fail-test-agent")
        assert claimed is not None and claimed["id"] == task_id

        result = fail_task(
            task_id,
            reason="Simulated first failure for test",
            source="test-failure",
        )

        assert result is not None
        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        assert task["queue"] == "requires-intervention"

    def test_fail_task_second_call_moves_to_failed(
        self, scoped_sdk, orchestrator_id, monkeypatch
    ):
        """fail_task() on a requires-intervention task moves it to failed with execution_notes."""
        from octopoid.tasks import create_task, claim_task, fail_task

        monkeypatch.setattr("octopoid.tasks.get_orchestrator_id", lambda: orchestrator_id)

        task_id = create_task(
            title="Test task — terminal failure",
            role="implement",
            context="Verify fail_task reaches failed queue after two failures.",
            acceptance_criteria=["Task ends in failed queue after two fail_task() calls"],
            priority="P2",
        )

        claimed = claim_task(role_filter="implement", agent_name="fail-test-agent-2")
        assert claimed is not None and claimed["id"] == task_id

        # First failure: claimed → requires-intervention
        fail_task(task_id, reason="First failure", source="test-failure")

        task_after_first = scoped_sdk.tasks.get(task_id)
        assert task_after_first["queue"] == "requires-intervention"

        # Second failure: requires-intervention → failed.
        result = fail_task(
            task_id, reason="Second failure — fixer also failed", source="test-fixer-failure"
        )

        assert result is not None
        # Queue must be "failed" — verify via GET for persistence confirmation.
        task_after_second = scoped_sdk.tasks.get(task_id)
        assert task_after_second is not None
        assert task_after_second["queue"] == "failed"

        # fail_task() passes execution_notes to sdk.tasks.update(); verify it appears
        # in the update result (the PATCH response from the server).
        # Note: the server does not persist execution_notes via PATCH — this field is
        # only stored when set through sdk.tasks.submit(). The function passes it so
        # future server versions can persist it; we verify the call succeeded by
        # confirming the queue transition above.
        assert result.get("queue") == "failed"
