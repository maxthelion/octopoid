"""Review tracking utilities for the gatekeeper review system.

Manages review state in the filesystem at:
    .orchestrator/shared/reviews/TASK-{id}/
        meta.json           - Review status and metadata
        checks/
            architecture.json
            testing.json
            qa.json
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import get_orchestrator_dir


def get_reviews_dir() -> Path:
    """Get the reviews root directory.

    Returns:
        Path to .orchestrator/shared/reviews/
    """
    reviews_dir = get_orchestrator_dir() / "shared" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    return reviews_dir


def get_review_dir(task_id: str) -> Path:
    """Get the review tracking directory for a specific task.

    Args:
        task_id: Task identifier

    Returns:
        Path to .orchestrator/shared/reviews/TASK-{id}/
    """
    review_dir = get_reviews_dir() / f"TASK-{task_id}"
    return review_dir


def init_task_review(
    task_id: str,
    branch: str,
    base_branch: str = "main",
    required_checks: list[str] | None = None,
) -> Path:
    """Initialize review tracking for a task.

    Creates the directory structure and meta.json for tracking
    gatekeeper review results.

    Args:
        task_id: Task identifier
        branch: The task's feature branch
        base_branch: The base branch to diff against
        required_checks: List of required check names (default: architecture, testing, qa)

    Returns:
        Path to the review directory
    """
    if required_checks is None:
        required_checks = ["architecture", "testing", "qa"]

    review_dir = get_review_dir(task_id)
    checks_dir = review_dir / "checks"
    checks_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "task_id": task_id,
        "branch": branch,
        "base_branch": base_branch,
        "initialized_at": datetime.now().isoformat(),
        "required_checks": required_checks,
        "status": "in_progress",
    }

    meta_path = review_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))

    # Create pending check files
    for check_name in required_checks:
        check_path = checks_dir / f"{check_name}.json"
        check_data = {
            "check_name": check_name,
            "status": "pending",
            "summary": "",
            "details": "",
            "submitted_at": None,
            "submitted_by": None,
        }
        check_path.write_text(json.dumps(check_data, indent=2))

    return review_dir


def load_review_meta(task_id: str) -> dict[str, Any] | None:
    """Load review metadata for a task.

    Args:
        task_id: Task identifier

    Returns:
        Review metadata dict or None if not initialized
    """
    meta_path = get_review_dir(task_id) / "meta.json"
    if not meta_path.exists():
        return None

    try:
        return json.loads(meta_path.read_text())
    except (json.JSONDecodeError, IOError):
        return None


def save_review_meta(task_id: str, meta: dict[str, Any]) -> None:
    """Save review metadata for a task.

    Args:
        task_id: Task identifier
        meta: Metadata dict to save
    """
    meta_path = get_review_dir(task_id) / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))


def record_review_result(
    task_id: str,
    check_name: str,
    status: str,
    summary: str,
    details: str = "",
    submitted_by: str | None = None,
) -> Path:
    """Record a single check result.

    Args:
        task_id: Task identifier
        check_name: Name of the check (architecture, testing, qa)
        status: Result status (pass, fail, pending)
        summary: One-line summary
        details: Full markdown report
        submitted_by: Name of the agent that submitted

    Returns:
        Path to the check result file
    """
    checks_dir = get_review_dir(task_id) / "checks"
    checks_dir.mkdir(parents=True, exist_ok=True)

    check_data = {
        "check_name": check_name,
        "status": status,
        "summary": summary,
        "details": details,
        "submitted_at": datetime.now().isoformat(),
        "submitted_by": submitted_by,
    }

    check_path = checks_dir / f"{check_name}.json"
    check_path.write_text(json.dumps(check_data, indent=2))

    return check_path


def load_check_result(task_id: str, check_name: str) -> dict[str, Any] | None:
    """Load a single check result.

    Args:
        task_id: Task identifier
        check_name: Name of the check

    Returns:
        Check result dict or None if not found
    """
    check_path = get_review_dir(task_id) / "checks" / f"{check_name}.json"
    if not check_path.exists():
        return None

    try:
        return json.loads(check_path.read_text())
    except (json.JSONDecodeError, IOError):
        return None


def all_reviews_complete(task_id: str) -> bool:
    """Check if all required checks have a final status (pass or fail).

    Args:
        task_id: Task identifier

    Returns:
        True if all checks are complete (no pending checks remain)
    """
    meta = load_review_meta(task_id)
    if not meta:
        return False

    for check_name in meta.get("required_checks", []):
        result = load_check_result(task_id, check_name)
        if not result or result.get("status") == "pending":
            return False

    return True


def all_reviews_passed(task_id: str) -> tuple[bool, list[str]]:
    """Check if all required checks passed.

    Args:
        task_id: Task identifier

    Returns:
        Tuple of (all_passed, list_of_failed_check_names)
    """
    meta = load_review_meta(task_id)
    if not meta:
        return False, []

    failed = []
    for check_name in meta.get("required_checks", []):
        result = load_check_result(task_id, check_name)
        if not result or result.get("status") != "pass":
            failed.append(check_name)

    return len(failed) == 0, failed


def get_review_feedback(task_id: str) -> str:
    """Aggregate feedback from all failed checks into markdown.

    Args:
        task_id: Task identifier

    Returns:
        Formatted markdown feedback string (empty if all passed)
    """
    meta = load_review_meta(task_id)
    if not meta:
        return ""

    feedback_parts = []
    for check_name in meta.get("required_checks", []):
        result = load_check_result(task_id, check_name)
        if not result:
            continue

        status = result.get("status", "pending")
        summary = result.get("summary", "")
        details = result.get("details", "")
        submitted_at = result.get("submitted_at", "")

        if status == "fail":
            part = f"### {check_name.title()} Review ({submitted_at})\n\n"
            part += f"**REJECTED** - {summary}\n"
            if details:
                part += f"\n{details}\n"
            feedback_parts.append(part)
        elif status == "pass":
            feedback_parts.append(
                f"### {check_name.title()} Review\n\n**PASSED** - {summary}\n"
            )

    return "\n".join(feedback_parts)


def cleanup_review(task_id: str) -> bool:
    """Clean up review tracking directory for a completed task.

    Args:
        task_id: Task identifier

    Returns:
        True if cleanup was performed
    """
    import shutil

    review_dir = get_review_dir(task_id)
    if review_dir.exists():
        shutil.rmtree(review_dir)
        return True
    return False


def has_active_review(task_id: str) -> bool:
    """Check if a task has an active (in-progress) review.

    Args:
        task_id: Task identifier

    Returns:
        True if review is initialized and in progress
    """
    meta = load_review_meta(task_id)
    if not meta:
        return False
    return meta.get("status") == "in_progress"
