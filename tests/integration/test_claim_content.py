"""Integration tests for task content after claiming.

Verifies that the orchestrator's claim_task() function returns task content
read from the local task file. The API stores just the filename (not content),
so the orchestrator must read the file from .octopoid/tasks/ after claiming.
"""

from pathlib import Path
from unittest.mock import patch

import pytest


# Task file content in the format that parse_task_file() expects
TASK_FILE_CONTENT = """\
# [TASK-claim-content-001] Test task with real content

ROLE: implement
PRIORITY: P1
BRANCH: main

## Context

The widget factory produces widgets too slowly.

## Acceptance Criteria

- [ ] Widget throughput increased by 2x
- [ ] No regression in widget quality
- [ ] Tests pass
"""


class TestClaimReturnsContent:
    """After claiming, task dict must include content from the task file."""

    def test_claim_task_includes_file_content(self, sdk, orchestrator_id, clean_tasks, tmp_path):
        """claim_task() must return content read from the task file.

        The API returns file_path (just a filename) but not the file's content.
        The orchestrator resolves the filename to .octopoid/tasks/<filename>
        and reads the content so the prompt sent to Claude has task details.
        """
        # Create tasks directory and write the task file
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        filename = "claim-content-001.md"
        task_file = tasks_dir / filename
        task_file.write_text(TASK_FILE_CONTENT)

        # Register the task with the API — just the filename, not the full path
        sdk.tasks.create(
            id="claim-content-001",
            file_path=filename,
            title="Test task with real content",
            role="implement",
            priority="P1",
            branch="main",
        )

        # Patch tasks dir and orchestrator ID (SDK already patched by conftest)
        import orchestrator.queue_utils as qu
        import orchestrator.tasks as tasks_mod

        with patch.object(tasks_mod, "get_tasks_file_dir", return_value=tasks_dir), \
             patch.object(qu, "get_orchestrator_id", return_value=orchestrator_id):

            task = qu.claim_task(role_filter="implement", agent_name="test-agent")

            # Fundamental assertions
            assert task is not None, "claim_task returned None — no task claimed"
            assert task["id"] == "claim-content-001"

            # THE KEY ASSERTION: content must be populated from the file
            assert "content" in task, (
                "claim_task() did not populate 'content'. "
                "The prompt sent to Claude will have empty ## Task Details."
            )
            assert len(task["content"]) > 0, "task content is empty string"
            assert "widget factory" in task["content"], (
                f"task content doesn't contain expected text. Got: {task['content'][:200]}"
            )

            # file_path should be resolved to absolute path
            assert task.get("file_path") is not None, (
                "claim response missing file_path"
            )
            assert Path(task["file_path"]).is_absolute(), (
                f"file_path should be absolute, got: {task['file_path']}"
            )

    def test_claim_task_raises_on_missing_file(self, sdk, orchestrator_id, clean_tasks, tmp_path):
        """claim_task() must raise FileNotFoundError if the file doesn't exist.

        The API is the source of truth for file_path. If the file doesn't
        exist in .octopoid/tasks/, that's a hard error — not something to
        silently skip.
        """
        # Create empty tasks directory (no file on disk)
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        # Register task with a filename that doesn't exist on disk
        sdk.tasks.create(
            id="claim-content-002",
            file_path="nonexistent-file.md",
            title="Missing file test",
            role="implement",
            priority="P1",
            branch="main",
        )

        import orchestrator.queue_utils as qu
        import orchestrator.tasks as tasks_mod

        with patch.object(tasks_mod, "get_tasks_file_dir", return_value=tasks_dir), \
             patch.object(qu, "get_orchestrator_id", return_value=orchestrator_id):

            with pytest.raises(FileNotFoundError):
                qu.claim_task(role_filter="implement", agent_name="test-agent")
