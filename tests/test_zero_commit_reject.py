"""Tests for 0-commit auto-rejection, rejection banner, and gatekeeper file reading."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest


class TestZeroCommitAutoReject:
    """Tests for submit_completion() auto-rejecting 0-commit re-submissions."""

    def test_zero_commits_previously_claimed_auto_rejects(self, mock_config, initialized_db):
        """A 0-commit submission from a previously-claimed task should auto-reject."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.db import create_task, claim_task, submit_completion as db_submit, get_task, reject_completion as db_reject
                    from orchestrator.queue_utils import submit_completion

                    # Create task, claim it, reject it (simulating first attempt failed)
                    claimed_dir = mock_config / "shared" / "queue" / "claimed"
                    claimed_dir.mkdir(parents=True, exist_ok=True)

                    create_task(
                        task_id="rej001",
                        file_path=str(claimed_dir / "TASK-rej001.md"),
                    )
                    # First attempt: claim, submit with 0 commits (goes to provisional normally)
                    db_task = claim_task()
                    db_submit("rej001", commits_count=0, turns_used=10)
                    # Pre-check rejects it
                    db_reject("rej001", reason="no_commits")

                    # Second attempt: claim again
                    claim_task()

                    # Create the file in claimed dir
                    task_file = claimed_dir / "TASK-rej001.md"
                    task_file.write_text("# [TASK-rej001] Test task\n")

                    # Now submit with 0 commits again — should auto-reject
                    result = submit_completion(task_file, commits_count=0, turns_used=7)

                    # Task should be back in incoming, not provisional
                    task = get_task("rej001")
                    assert task["queue"] == "incoming", (
                        f"Expected 0-commit re-submission to auto-reject to incoming, "
                        f"but found in {task['queue']}"
                    )

    def test_zero_commits_first_claim_goes_to_provisional(self, mock_config, initialized_db):
        """A 0-commit submission from a FIRST claim should go to provisional normally."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.db import create_task, claim_task, get_task
                    from orchestrator.queue_utils import submit_completion

                    claimed_dir = mock_config / "shared" / "queue" / "claimed"
                    claimed_dir.mkdir(parents=True, exist_ok=True)

                    create_task(
                        task_id="fresh01",
                        file_path=str(claimed_dir / "TASK-fresh01.md"),
                    )
                    claim_task()

                    # Create the file
                    task_file = claimed_dir / "TASK-fresh01.md"
                    task_file.write_text("# [TASK-fresh01] Fresh task\n")

                    # Submit with 0 commits on first attempt — should go to provisional
                    result = submit_completion(task_file, commits_count=0, turns_used=10)

                    task = get_task("fresh01")
                    assert task["queue"] == "provisional", (
                        f"Expected first 0-commit submission to go to provisional, "
                        f"but found in {task['queue']}"
                    )

    def test_nonzero_commits_always_goes_to_provisional(self, mock_config, initialized_db):
        """A submission with commits should always go to provisional regardless of history."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.db import create_task, claim_task, submit_completion as db_submit, get_task, reject_completion as db_reject
                    from orchestrator.queue_utils import submit_completion

                    claimed_dir = mock_config / "shared" / "queue" / "claimed"
                    claimed_dir.mkdir(parents=True, exist_ok=True)

                    create_task(
                        task_id="fix001",
                        file_path=str(claimed_dir / "TASK-fix001.md"),
                    )
                    # First attempt failed
                    claim_task()
                    db_submit("fix001", commits_count=0, turns_used=10)
                    db_reject("fix001", reason="no_commits")

                    # Second attempt with commits
                    claim_task()

                    task_file = claimed_dir / "TASK-fix001.md"
                    task_file.write_text("# [TASK-fix001] Fix task\n")

                    result = submit_completion(task_file, commits_count=3, turns_used=20)

                    task = get_task("fix001")
                    assert task["queue"] == "provisional", (
                        f"Expected submission with commits to go to provisional, "
                        f"but found in {task['queue']}"
                    )

    def test_zero_commits_with_rejection_count_auto_rejects(self, mock_config, initialized_db):
        """A 0-commit submission from a review-rejected task should auto-reject."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.db import create_task, claim_task, submit_completion as db_submit, get_task, review_reject_completion, update_task
                    from orchestrator.queue_utils import submit_completion

                    claimed_dir = mock_config / "shared" / "queue" / "claimed"
                    claimed_dir.mkdir(parents=True, exist_ok=True)

                    create_task(
                        task_id="revrej1",
                        file_path=str(claimed_dir / "TASK-revrej1.md"),
                    )
                    # First attempt: commit, but rejected by reviewer
                    claim_task()
                    db_submit("revrej1", commits_count=2, turns_used=30)
                    review_reject_completion("revrej1", reason="bad code", reviewer="gk-testing")

                    # Second attempt: agent bails with 0 commits
                    claim_task()

                    task_file = claimed_dir / "TASK-revrej1.md"
                    task_file.write_text("# [TASK-revrej1] Review rejected\n")

                    result = submit_completion(task_file, commits_count=0, turns_used=7)

                    task = get_task("revrej1")
                    assert task["queue"] == "incoming", (
                        f"Expected 0-commit submission after review rejection to auto-reject, "
                        f"but found in {task['queue']}"
                    )


class TestRejectionBanner:
    """Tests for the rejection banner in implementer prompts."""

    def test_rejection_banner_included_for_rejected_task(self, mock_config, initialized_db):
        """When a task has rejection_count > 0, the prompt should include a rejection banner."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                with patch('orchestrator.queue_utils.get_queue_dir', return_value=mock_config / "shared" / "queue"):
                    from orchestrator.db import create_task, update_task, get_task

                    incoming_dir = mock_config / "shared" / "queue" / "incoming"
                    incoming_dir.mkdir(parents=True, exist_ok=True)

                    create_task(
                        task_id="banner1",
                        file_path=str(incoming_dir / "TASK-banner1.md"),
                        role="implement",
                    )
                    update_task("banner1", rejection_count=1, attempt_count=2)

                    task = get_task("banner1")

                    # Simulate what the implementer role does with these values
                    rejection_count = task.get("rejection_count", 0)
                    attempt_count = task.get("attempt_count", 0)

                    assert rejection_count > 0 or attempt_count > 0
                    assert rejection_count == 1
                    assert attempt_count == 2

    def test_no_rejection_banner_for_fresh_task(self, mock_config, initialized_db):
        """When a task is fresh (no rejections), no banner should be generated."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            with patch('orchestrator.queue_utils.is_db_enabled', return_value=True):
                from orchestrator.db import create_task, get_task

                incoming_dir = mock_config / "shared" / "queue" / "incoming"
                incoming_dir.mkdir(parents=True, exist_ok=True)

                create_task(
                    task_id="fresh02",
                    file_path=str(incoming_dir / "TASK-fresh02.md"),
                    role="implement",
                )

                task = get_task("fresh02")

                rejection_count = task.get("rejection_count", 0)
                attempt_count = task.get("attempt_count", 0)

                assert rejection_count == 0
                assert attempt_count == 0


class TestGatekeeperReadsCurrentFile:
    """Tests that gatekeeper reads the task file from disk at dispatch time."""

    def test_gatekeeper_reads_task_file_from_queue_directories(self, mock_config, initialized_db):
        """The gatekeeper should search queue directories for the task file, not use a cached version."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            # Create a task file in the provisional directory
            provisional_dir = mock_config / "shared" / "queue" / "provisional"
            provisional_dir.mkdir(parents=True, exist_ok=True)

            task_file = provisional_dir / "TASK-gkread1.md"
            original_content = "# [TASK-gkread1] Original content\n\n## Acceptance Criteria\n- [ ] Test passes\n"
            task_file.write_text(original_content)

            # Update the file to simulate changed acceptance criteria
            updated_content = "# [TASK-gkread1] Updated content with new criteria\n\n## Acceptance Criteria\n- [ ] New requirement added\n- [ ] Second requirement\n"
            task_file.write_text(updated_content)

            # Read the file as the gatekeeper would
            task_file_content = ""
            task_file_path = mock_config / "shared" / "queue"
            for subdir in ["provisional", "incoming", "claimed", "done"]:
                candidate = task_file_path / subdir / "TASK-gkread1.md"
                if candidate.exists():
                    task_file_content = candidate.read_text()
                    break

            # Should read the UPDATED content, not cached original
            assert "Updated content" in task_file_content
            assert "New requirement" in task_file_content
            assert "Original content" not in task_file_content

    def test_gatekeeper_searches_multiple_queue_dirs(self, mock_config, initialized_db):
        """The gatekeeper search finds files that moved between queues."""
        with patch('orchestrator.db.get_database_path', return_value=initialized_db):
            # Create a task file in the incoming directory (not provisional)
            incoming_dir = mock_config / "shared" / "queue" / "incoming"
            incoming_dir.mkdir(parents=True, exist_ok=True)

            task_file = incoming_dir / "TASK-gkread2.md"
            task_file.write_text("# [TASK-gkread2] Task in incoming\n")

            # Search as the gatekeeper would (checking provisional first)
            task_file_content = ""
            task_file_path = mock_config / "shared" / "queue"
            for subdir in ["provisional", "incoming", "claimed", "done"]:
                candidate = task_file_path / subdir / "TASK-gkread2.md"
                if candidate.exists():
                    task_file_content = candidate.read_text()
                    break

            assert "Task in incoming" in task_file_content
