"""Tests for accept_all.py script behavior.

These tests verify that the acceptance script correctly routes tasks:
- Tasks with commits -> accepted (done queue)
- Burned-out tasks (0 commits, high turns) -> recycled to breakdown
- Depth-capped tasks -> accepted with note for human review
"""

import re
import pytest
from pathlib import Path
from unittest.mock import patch


class TestAcceptAllRouting:
    """Tests for accept_all routing logic."""

    def test_normal_task_accepted(self, mock_config, sample_project_with_tasks):
        """Task with commits is accepted to done queue."""
        with patch('orchestrator.db.get_database_path', return_value=sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import is_burned_out

                    # A task with commits should NOT be burned out
                    assert is_burned_out(commits_count=3, turns_used=50) is False

    def test_burned_task_detected(self, mock_config, sample_project_with_tasks):
        """Task with 0 commits and high turns is detected as burned out."""
        with patch('orchestrator.db.get_database_path', return_value=sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import is_burned_out

                    # A task with 0 commits and 100 turns IS burned out
                    assert is_burned_out(commits_count=0, turns_used=100) is True

    def test_burned_task_recycled_not_accepted(self, mock_config, sample_project_with_tasks):
        """Burned-out task gets recycled, not accepted to done."""
        with patch('orchestrator.db.get_database_path', return_value=sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import recycle_to_breakdown, is_burned_out
                    from orchestrator.db import get_task

                    burned = sample_project_with_tasks["burned_task"]
                    task = get_task(burned["id"])

                    # Verify it's detected as burned out
                    assert is_burned_out(
                        commits_count=task.get("commits_count", 0),
                        turns_used=task.get("turns_used", 0),
                    ) is True

                    # Recycle it
                    result = recycle_to_breakdown(burned["path"])
                    assert result is not None
                    assert result["action"] == "recycled"

                    # Original should NOT be in done queue
                    updated_task = get_task(burned["id"])
                    assert updated_task["queue"] == "recycled"
                    assert updated_task["queue"] != "done"

    def test_mixed_batch_routes_correctly(self, mock_config, sample_project_with_tasks):
        """A batch with both normal and burned tasks routes each correctly."""
        with patch('orchestrator.db.get_database_path', return_value=sample_project_with_tasks["completed_tasks"][0]["path"].parent.parent.parent.parent / "state.db"):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.queue_utils import (
                        is_burned_out,
                        recycle_to_breakdown,
                        accept_completion,
                    )
                    from orchestrator.db import get_task, update_task, update_task_queue

                    # Create a "normal" task in provisional (has commits)
                    prov_dir = mock_config / "shared" / "queue" / "provisional"
                    normal_path = prov_dir / "TASK-norm0001.md"
                    normal_path.write_text(
                        "# [TASK-norm0001] Normal provisional task\n\n"
                        "ROLE: implement\nPRIORITY: P1\nBRANCH: feature/test1\n"
                        "PROJECT: PROJ-test1\n\n"
                        "## Context\nNormal work.\n\n"
                        "## Acceptance Criteria\n- [ ] Done\n"
                    )
                    from orchestrator.db import create_task as db_create_task
                    db_create_task(task_id="norm0001", file_path=str(normal_path), project_id="PROJ-test1", role="implement")
                    update_task_queue("norm0001", "provisional", commits_count=2, turns_used=30)

                    burned = sample_project_with_tasks["burned_task"]

                    # Simulate the routing logic from accept_all.py
                    tasks_to_process = [
                        {"id": "norm0001", "path": normal_path, "commits": 2, "turns": 30},
                        {"id": burned["id"], "path": burned["path"], "commits": 0, "turns": 100},
                    ]

                    accepted = []
                    recycled = []

                    for t in tasks_to_process:
                        if is_burned_out(commits_count=t["commits"], turns_used=t["turns"]):
                            result = recycle_to_breakdown(t["path"])
                            if result:
                                recycled.append(t["id"])
                        else:
                            accept_completion(t["path"], accepted_by="test")
                            accepted.append(t["id"])

                    assert "norm0001" in accepted
                    assert burned["id"] in recycled
                    assert len(accepted) == 1
                    assert len(recycled) == 1
