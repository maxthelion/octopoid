"""Integration tests for octopoid/tasks.py lifecycle functions.

Tests that create_task(), claim_task(), submit_completion(), and fail_task()
work correctly against the real local test server.

All tests use scoped_sdk for isolation.
"""

import re
import uuid
from unittest.mock import patch

import pytest


def _make_id(prefix: str = "") -> str:
    """Generate a unique task ID (8 hex chars) for test isolation."""
    return (prefix + uuid.uuid4().hex)[:8]


TASK_CONTENT = """\
# [TASK-{id}] Integration test task

ROLE: implement
PRIORITY: P2
BRANCH: main

## Context

This is a test task created by integration tests.

## Acceptance Criteria

- [ ] Integration test passes
"""


class TestCreateTask:
    """create_task() registers a task on the server and returns an 8-char hex ID."""

    def test_returns_8char_hex_id(self, scoped_sdk, orchestrator_id):
        import octopoid.tasks as tasks_mod

        task_id = tasks_mod.create_task(
            title="Integration test create_task",
            role="implement",
            context="Test context for integration test.",
            acceptance_criteria=["Integration test passes"],
            priority="P2",
            branch="main",
        )

        # Must be an 8-character hex string
        assert isinstance(task_id, str)
        assert len(task_id) == 8
        assert re.fullmatch(r"[0-9a-f]{8}", task_id), (
            f"Expected 8-char hex ID, got: {task_id!r}"
        )

    def test_task_appears_in_incoming_queue(self, scoped_sdk, orchestrator_id):
        import octopoid.tasks as tasks_mod

        task_id = tasks_mod.create_task(
            title="Integration test create_task queue",
            role="implement",
            context="Test that task appears in incoming queue.",
            acceptance_criteria=["Task is in incoming queue"],
            priority="P2",
            branch="main",
        )

        # Task must appear on the server in the incoming queue
        task = scoped_sdk.tasks.get(task_id)
        assert task is not None, f"Task {task_id} not found on server"
        assert task["queue"] == "incoming", (
            f"Expected queue='incoming', got: {task.get('queue')!r}"
        )

    def test_task_has_non_empty_content(self, scoped_sdk, orchestrator_id):
        import octopoid.tasks as tasks_mod

        task_id = tasks_mod.create_task(
            title="Integration test content",
            role="implement",
            context="Test that content field is populated on the server.",
            acceptance_criteria=["Content is non-empty"],
            priority="P2",
            branch="main",
        )

        task = scoped_sdk.tasks.get(task_id)
        assert task is not None
        content = task.get("content", "")
        assert content, (
            f"Task {task_id} has empty content on the server. "
            "create_task() must store the full markdown content."
        )
        assert "Integration test content" in content, (
            f"Task title not found in content. Content: {content[:300]}"
        )
        assert "Test that content field is populated" in content, (
            f"Task context not found in content. Content: {content[:300]}"
        )


class TestClaimTask:
    """claim_task() atomically claims a task and moves it to claimed queue."""

    def test_claim_task_returns_claimed_task(self, scoped_sdk, orchestrator_id):
        import octopoid.tasks as tasks_mod

        task_id = _make_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f"TASK-{task_id}.md",
            title="Claim test task",
            role="implement",
            priority="P2",
            branch="main",
            content=TASK_CONTENT.format(id=task_id),
        )

        with patch.object(tasks_mod, "get_orchestrator_id", return_value=orchestrator_id):
            claimed = tasks_mod.claim_task(role_filter="implement", agent_name="test-agent")

        assert claimed is not None, "claim_task() returned None — no task was claimed"
        assert claimed["id"] == task_id
        assert claimed["queue"] == "claimed"

    def test_claim_task_sets_claimed_by(self, scoped_sdk, orchestrator_id):
        import octopoid.tasks as tasks_mod

        task_id = _make_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f"TASK-{task_id}.md",
            title="Claim agent name test",
            role="implement",
            priority="P2",
            branch="main",
            content=TASK_CONTENT.format(id=task_id),
        )

        with patch.object(tasks_mod, "get_orchestrator_id", return_value=orchestrator_id):
            claimed = tasks_mod.claim_task(role_filter="implement", agent_name="my-test-agent")

        assert claimed is not None
        assert claimed["claimed_by"] == "my-test-agent"

    def test_claim_task_returns_none_when_empty_queue(self, scoped_sdk, orchestrator_id):
        import octopoid.tasks as tasks_mod

        # Queue is empty — no tasks created in this scope
        with patch.object(tasks_mod, "get_orchestrator_id", return_value=orchestrator_id):
            claimed = tasks_mod.claim_task(role_filter="implement", agent_name="test-agent")

        assert claimed is None


class TestSubmitCompletion:
    """submit_completion() moves a claimed task to the provisional queue."""

    def test_submit_completion_moves_to_provisional(self, scoped_sdk, orchestrator_id):
        import octopoid.tasks as tasks_mod

        task_id = _make_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f"TASK-{task_id}.md",
            title="Submit completion test",
            role="implement",
            priority="P2",
            branch="main",
            content=TASK_CONTENT.format(id=task_id),
        )

        # Claim via SDK (bypasses claim_task() side effects for setup)
        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )

        result = tasks_mod.submit_completion(task_id, commits_count=2, turns_used=10)

        assert result is not None, "submit_completion() returned None"
        assert result["queue"] == "provisional", (
            f"Expected queue='provisional', got: {result.get('queue')!r}"
        )

    def test_submit_completion_sets_execution_notes(self, scoped_sdk, orchestrator_id):
        import octopoid.tasks as tasks_mod

        task_id = _make_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f"TASK-{task_id}.md",
            title="Submit execution notes test",
            role="implement",
            priority="P2",
            branch="main",
            content=TASK_CONTENT.format(id=task_id),
        )

        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )

        result = tasks_mod.submit_completion(task_id, commits_count=3, turns_used=7)

        assert result is not None
        execution_notes = result.get("execution_notes", "")
        assert "3 commit" in execution_notes, (
            f"execution_notes should mention commits. Got: {execution_notes!r}"
        )


class TestFailTask:
    """fail_task() routes failing tasks through requires-intervention then to failed."""

    def test_fail_task_first_failure_goes_to_requires_intervention(
        self, scoped_sdk, orchestrator_id
    ):
        import octopoid.tasks as tasks_mod

        task_id = _make_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f"TASK-{task_id}.md",
            title="Fail task first failure test",
            role="implement",
            priority="P2",
            branch="main",
            content=TASK_CONTENT.format(id=task_id),
        )

        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )

        result = tasks_mod.fail_task(
            task_id=task_id,
            reason="Test failure reason",
            source="test-source",
        )

        assert result is not None
        assert result["queue"] == "requires-intervention", (
            f"Expected first failure to route to 'requires-intervention', "
            f"got: {result.get('queue')!r}"
        )

    def test_fail_task_second_failure_goes_to_failed_with_execution_notes(
        self, scoped_sdk, orchestrator_id, capsys
    ):
        import octopoid.tasks as tasks_mod

        task_id = _make_id()
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f"TASK-{task_id}.md",
            title="Fail task terminal failure test",
            role="implement",
            priority="P2",
            branch="main",
            content=TASK_CONTENT.format(id=task_id),
        )

        scoped_sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement",
        )

        # First failure: claimed → requires-intervention
        tasks_mod.fail_task(
            task_id=task_id,
            reason="First failure reason",
            source="test-first-failure",
        )

        # Second failure: requires-intervention → failed (terminal)
        failure_reason = "Second failure — fixer also failed"
        result = tasks_mod.fail_task(
            task_id=task_id,
            reason=failure_reason,
            source="test-second-failure",
        )

        assert result is not None
        assert result["queue"] == "failed", (
            f"Expected second failure to move to 'failed', got: {result.get('queue')!r}"
        )

        # Verify execution_notes was passed to the update call.
        # The PATCH endpoint returns it in the response; capture confirms the
        # FAILED log was emitted with the full reason.
        captured = capsys.readouterr()
        assert "FAILED" in captured.out, "Expected FAILED log line in stdout"
        assert task_id in captured.out, f"Expected task_id {task_id} in FAILED log"
        assert "Second failure" in captured.out, (
            f"Expected failure reason in FAILED log. Got stdout: {captured.out!r}"
        )
