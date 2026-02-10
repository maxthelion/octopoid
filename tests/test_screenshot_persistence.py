"""Tests for QA screenshot persistence."""

from orchestrator import db
from orchestrator.queue_utils import create_task


def test_record_check_result_with_screenshots(initialized_db):
    """Test that screenshots are persisted in check results."""
    # Create a task
    task_file = create_task(
        title="Test task",
        context="Test context",
        acceptance_criteria=["Criterion 1"],
        role="implement",
        priority="P1",
    )
    # Extract task ID from filename: TASK-abc123.md -> abc123
    task_id = task_file.stem[5:]  # Skip "TASK-" prefix

    # Record check result with screenshots
    screenshot_paths = [
        f".orchestrator/agents/gk-qa/screenshots/TASK-{task_id}/01-initial.png",
        f".orchestrator/agents/gk-qa/screenshots/TASK-{task_id}/02-bug.png",
    ]

    result = db.record_check_result(
        task_id=task_id,
        check_name="qa",
        status="fail",
        summary="Bug found in UI",
        screenshots=screenshot_paths,
    )

    # Verify screenshots are stored in check results
    assert result is not None
    check_results = result.get("check_results", {})
    assert "qa" in check_results

    qa_result = check_results["qa"]
    assert qa_result["status"] == "fail"
    assert qa_result["summary"] == "Bug found in UI"
    assert qa_result["screenshots"] == screenshot_paths


def test_record_check_result_without_screenshots(initialized_db):
    """Test that check results work without screenshots (backward compatibility)."""
    # Create a task
    task_file = create_task(
        title="Test task",
        context="Test context",
        acceptance_criteria=["Criterion 1"],
        role="implement",
        priority="P1",
    )
    task_id = task_file.stem[5:]  # Skip "TASK-" prefix

    # Record check result without screenshots
    result = db.record_check_result(
        task_id=task_id,
        check_name="qa",
        status="pass",
        summary="Everything looks good",
    )

    # Verify result is recorded without screenshots field
    assert result is not None
    check_results = result.get("check_results", {})
    assert "qa" in check_results

    qa_result = check_results["qa"]
    assert qa_result["status"] == "pass"
    assert qa_result["summary"] == "Everything looks good"
    assert "screenshots" not in qa_result


def test_get_check_feedback_includes_screenshots(initialized_db):
    """Test that get_check_feedback includes screenshot paths in output."""
    # Create a task
    task_file = create_task(
        title="Test task",
        context="Test context",
        acceptance_criteria=["Criterion 1"],
        role="implement",
        priority="P1",
    )
    task_id = task_file.stem[5:]  # Skip "TASK-" prefix

    # Set checks on the task
    db.update_task(task_id, checks=["qa"])

    # Record failed check with screenshots
    screenshot_paths = [
        f".orchestrator/agents/gk-qa/screenshots/TASK-{task_id}/01-initial.png",
        f".orchestrator/agents/gk-qa/screenshots/TASK-{task_id}/02-bug.png",
    ]

    db.record_check_result(
        task_id=task_id,
        check_name="qa",
        status="fail",
        summary="Center line not visible",
        screenshots=screenshot_paths,
    )

    # Get aggregated feedback
    feedback = db.get_check_feedback(task_id)

    # Verify feedback includes screenshots
    assert feedback
    assert "qa" in feedback
    assert "Center line not visible" in feedback
    assert screenshot_paths[0] in feedback
    assert screenshot_paths[1] in feedback
    assert "**Screenshots:**" in feedback


def test_get_check_feedback_without_screenshots(initialized_db):
    """Test that get_check_feedback works without screenshots."""
    # Create a task
    task_file = create_task(
        title="Test task",
        context="Test context",
        acceptance_criteria=["Criterion 1"],
        role="implement",
        priority="P1",
    )
    task_id = task_file.stem[5:]  # Skip "TASK-" prefix

    # Set checks on the task
    db.update_task(task_id, checks=["architecture"])

    # Record failed check without screenshots
    db.record_check_result(
        task_id=task_id,
        check_name="architecture",
        status="fail",
        summary="Poor separation of concerns",
    )

    # Get aggregated feedback
    feedback = db.get_check_feedback(task_id)

    # Verify feedback is generated (without screenshots section)
    assert feedback
    assert "architecture" in feedback
    assert "Poor separation of concerns" in feedback
    assert "Screenshots" not in feedback
