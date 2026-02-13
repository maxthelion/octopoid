"""Integration tests for execution_notes field."""

import pytest
from octopoid_sdk import OctopoidSDK


class TestExecutionNotes:
    """Test that execution_notes field is properly populated and retrieved."""

    def test_execution_notes_populated_on_submit(self, sdk, orchestrator_id, clean_tasks):
        """Execution notes are populated when task is submitted."""
        # Create and claim task
        sdk.tasks.create(
            id="notes-test-001",
            file_path="/tmp/notes-test-001.md",
            title="Execution Notes Test",
            role="implement"
        )

        claimed = sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement"
        )
        assert claimed['id'] == "notes-test-001"

        # Submit with execution notes
        execution_summary = "Implemented feature X. Fixed 2 bugs in module Y. All tests passing."
        submitted = sdk.tasks.submit(
            task_id="notes-test-001",
            commits_count=3,
            turns_used=5,
            execution_notes=execution_summary
        )

        # Verify execution_notes is stored
        assert submitted['queue'] == 'provisional'
        assert submitted.get('execution_notes') == execution_summary

        # Verify we can retrieve it
        task = sdk.tasks.get("notes-test-001")
        assert task is not None
        assert task.get('execution_notes') == execution_summary

    def test_execution_notes_persists_after_accept(self, sdk, orchestrator_id, clean_tasks):
        """Execution notes persist after task is accepted."""
        # Create, claim, and submit
        sdk.tasks.create(
            id="notes-persist-001",
            file_path="/tmp/notes-persist-001.md",
            title="Notes Persistence Test",
            role="implement"
        )

        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement"
        )

        execution_summary = "Created 5 commits. Refactored auth module. Tests passing."
        sdk.tasks.submit(
            task_id="notes-persist-001",
            commits_count=5,
            turns_used=10,
            execution_notes=execution_summary
        )

        # Accept task
        accepted = sdk.tasks.accept(
            task_id="notes-persist-001",
            accepted_by="test-gatekeeper"
        )

        # Verify execution_notes still present
        assert accepted['queue'] == 'done'
        assert accepted.get('execution_notes') == execution_summary

        # Verify retrieval still works
        task = sdk.tasks.get("notes-persist-001")
        assert task.get('execution_notes') == execution_summary

    def test_execution_notes_optional(self, sdk, orchestrator_id, clean_tasks):
        """Execution notes are optional - can submit without them."""
        # Create and claim
        sdk.tasks.create(
            id="no-notes-001",
            file_path="/tmp/no-notes-001.md",
            title="No Notes Test",
            role="implement"
        )

        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement"
        )

        # Submit without execution_notes
        submitted = sdk.tasks.submit(
            task_id="no-notes-001",
            commits_count=1,
            turns_used=2
        )

        # Should succeed, notes should be None or empty
        assert submitted['queue'] == 'provisional'
        # execution_notes may be None or not present
        notes = submitted.get('execution_notes')
        assert notes is None or notes == ""

    def test_execution_notes_with_special_characters(self, sdk, orchestrator_id, clean_tasks):
        """Execution notes can contain special characters and formatting."""
        sdk.tasks.create(
            id="notes-special-001",
            file_path="/tmp/notes-special-001.md",
            title="Special Chars Test",
            role="implement"
        )

        sdk.tasks.claim(
            orchestrator_id=orchestrator_id,
            agent_name="test-agent",
            role_filter="implement"
        )

        # Notes with special characters, line breaks, etc.
        complex_notes = """Multi-line summary:
- Fixed bug in auth.ts (line 42)
- Added validation for user@email.com format
- Updated tests: "test_auth_flow" & "test_validation"
- Commits: 3
"""
        submitted = sdk.tasks.submit(
            task_id="notes-special-001",
            commits_count=3,
            turns_used=7,
            execution_notes=complex_notes
        )

        # Verify it's stored correctly
        task = sdk.tasks.get("notes-special-001")
        assert task.get('execution_notes') == complex_notes

    def test_execution_notes_in_list_response(self, sdk, orchestrator_id, clean_tasks):
        """Execution notes appear in task list responses."""
        # Create multiple tasks with notes
        for i in range(3):
            task_id = f"list-notes-{i:03d}"
            sdk.tasks.create(
                id=task_id,
                file_path=f"/tmp/{task_id}.md",
                title=f"List Test {i}",
                role="implement"
            )

            sdk.tasks.claim(
                orchestrator_id=orchestrator_id,
                agent_name=f"agent-{i}",
                role_filter="implement"
            )

            sdk.tasks.submit(
                task_id=task_id,
                commits_count=i + 1,
                turns_used=(i + 1) * 2,
                execution_notes=f"Summary for task {i}"
            )

        # List tasks in provisional queue
        tasks = sdk.tasks.list(queue="provisional")
        assert len(tasks) >= 3

        # Find our tasks and verify notes
        our_tasks = [t for t in tasks if t['id'].startswith('list-notes-')]
        assert len(our_tasks) == 3

        for task in our_tasks:
            idx = int(task['id'].split('-')[-1])
            expected_notes = f"Summary for task {idx}"
            assert task.get('execution_notes') == expected_notes
