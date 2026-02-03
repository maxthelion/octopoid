"""PR tracking and gatekeeper utilities."""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from .config import get_gatekeeper_config, get_prs_dir
from .queue_utils import create_task

CheckStatus = Literal["pending", "running", "passed", "failed", "warning"]


def get_pr_dir(pr_number: int) -> Path:
    """Get the directory for a specific PR's gatekeeper data.

    Args:
        pr_number: The PR number

    Returns:
        Path to the PR's directory
    """
    pr_dir = get_prs_dir() / f"PR-{pr_number}"
    pr_dir.mkdir(parents=True, exist_ok=True)
    return pr_dir


def get_pr_checks_dir(pr_number: int) -> Path:
    """Get the checks directory for a PR.

    Args:
        pr_number: The PR number

    Returns:
        Path to the checks directory
    """
    checks_dir = get_pr_dir(pr_number) / "checks"
    checks_dir.mkdir(parents=True, exist_ok=True)
    return checks_dir


def detect_new_prs() -> list[dict[str, Any]]:
    """Detect PRs that need gatekeeper checks.

    Returns:
        List of PR info dictionaries for PRs needing checks
    """
    try:
        # Get open PRs
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--state", "open",
                "--json", "number,title,headRefName,baseRefName,author,updatedAt,labels"
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return []

        prs = json.loads(result.stdout)
        needs_check = []

        for pr in prs:
            pr_number = pr["number"]
            pr_dir = get_pr_dir(pr_number)
            meta_path = pr_dir / "meta.json"

            # Check if we've seen this PR before
            if meta_path.exists():
                with open(meta_path) as f:
                    meta = json.load(f)

                # Check if PR was updated since last check
                last_checked = meta.get("last_checked")
                pr_updated = pr.get("updatedAt")

                if last_checked and pr_updated:
                    if pr_updated <= last_checked:
                        # No updates since last check
                        if meta.get("status") not in ["pending", "running"]:
                            continue

            needs_check.append(pr)

        return needs_check

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError):
        return []


def get_pr_info(pr_number: int) -> dict[str, Any] | None:
    """Get detailed information about a PR.

    Args:
        pr_number: The PR number

    Returns:
        PR info dictionary or None if not found
    """
    try:
        result = subprocess.run(
            [
                "gh", "pr", "view", str(pr_number),
                "--json", "number,title,body,headRefName,baseRefName,author,files,commits,additions,deletions,changedFiles"
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return None

        return json.loads(result.stdout)

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def get_pr_diff(pr_number: int) -> str:
    """Get the diff for a PR.

    Args:
        pr_number: The PR number

    Returns:
        Diff as a string, or empty string on error
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number)],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            return ""

        return result.stdout

    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return ""


def init_pr_check(pr_number: int, pr_info: dict[str, Any]) -> Path:
    """Initialize a PR for gatekeeper checks.

    Args:
        pr_number: The PR number
        pr_info: PR info from get_pr_info or detect_new_prs

    Returns:
        Path to the PR's meta.json
    """
    pr_dir = get_pr_dir(pr_number)
    meta_path = pr_dir / "meta.json"

    gk_config = get_gatekeeper_config()

    meta = {
        "pr_number": pr_number,
        "title": pr_info.get("title", ""),
        "branch": pr_info.get("headRefName", ""),
        "base": pr_info.get("baseRefName", "main"),
        "author": pr_info.get("author", {}).get("login", "unknown"),
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "last_checked": None,
        "required_checks": gk_config["required_checks"],
        "optional_checks": gk_config["optional_checks"],
        "check_results": {},
    }

    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    return meta_path


def load_pr_meta(pr_number: int) -> dict[str, Any] | None:
    """Load PR metadata.

    Args:
        pr_number: The PR number

    Returns:
        Meta dictionary or None if not found
    """
    meta_path = get_pr_dir(pr_number) / "meta.json"
    if not meta_path.exists():
        return None

    try:
        with open(meta_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def save_pr_meta(pr_number: int, meta: dict[str, Any]) -> None:
    """Save PR metadata.

    Args:
        pr_number: The PR number
        meta: Meta dictionary to save
    """
    meta_path = get_pr_dir(pr_number) / "meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)


def record_check_result(
    pr_number: int,
    check_name: str,
    status: CheckStatus,
    summary: str,
    details: str | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> Path:
    """Record the result of a gatekeeper check.

    Args:
        pr_number: The PR number
        check_name: Name of the check (e.g., "lint", "tests")
        status: Check status
        summary: One-line summary
        details: Optional detailed report
        issues: Optional list of specific issues found

    Returns:
        Path to the check result file
    """
    checks_dir = get_pr_checks_dir(pr_number)
    check_path = checks_dir / f"{check_name}.md"

    # Build check result content
    status_emoji = {
        "pending": "⏳",
        "running": "🔄",
        "passed": "✅",
        "failed": "❌",
        "warning": "⚠️",
    }

    lines = [
        f"# {status_emoji.get(status, '❓')} {check_name.title()} Check",
        "",
        f"**Status:** {status}",
        f"**Time:** {datetime.now().isoformat()}",
        f"**Summary:** {summary}",
        "",
    ]

    if details:
        lines.extend(["## Details", "", details, ""])

    if issues:
        lines.extend(["## Issues", ""])
        for issue in issues:
            file_path = issue.get("file", "")
            line = issue.get("line", "")
            message = issue.get("message", "")
            severity = issue.get("severity", "error")

            location = f"{file_path}:{line}" if line else file_path
            lines.append(f"- **{severity}** `{location}`: {message}")
        lines.append("")

    check_path.write_text("\n".join(lines))

    # Update PR meta
    meta = load_pr_meta(pr_number)
    if meta:
        if "check_results" not in meta:
            meta["check_results"] = {}
        meta["check_results"][check_name] = {
            "status": status,
            "summary": summary,
            "time": datetime.now().isoformat(),
        }
        meta["last_checked"] = datetime.now().isoformat()
        save_pr_meta(pr_number, meta)

    return check_path


def get_check_results(pr_number: int) -> dict[str, dict[str, Any]]:
    """Get all check results for a PR.

    Args:
        pr_number: The PR number

    Returns:
        Dictionary mapping check name to result info
    """
    meta = load_pr_meta(pr_number)
    if not meta:
        return {}
    return meta.get("check_results", {})


def all_checks_complete(pr_number: int) -> bool:
    """Check if all required checks have completed.

    Args:
        pr_number: The PR number

    Returns:
        True if all required checks have a final status
    """
    meta = load_pr_meta(pr_number)
    if not meta:
        return False

    required = meta.get("required_checks", [])
    results = meta.get("check_results", {})

    for check in required:
        if check not in results:
            return False
        status = results[check].get("status")
        if status in ["pending", "running"]:
            return False

    return True


def all_checks_passed(pr_number: int) -> tuple[bool, list[str]]:
    """Check if all required checks passed.

    Args:
        pr_number: The PR number

    Returns:
        Tuple of (all_passed, list_of_failed_checks)
    """
    meta = load_pr_meta(pr_number)
    if not meta:
        return False, ["No PR metadata found"]

    required = meta.get("required_checks", [])
    results = meta.get("check_results", {})
    failed = []

    for check in required:
        if check not in results:
            failed.append(f"{check} (not run)")
        elif results[check].get("status") == "failed":
            failed.append(check)

    return len(failed) == 0, failed


def get_check_feedback(pr_number: int) -> str:
    """Get aggregated feedback from all failed checks.

    Args:
        pr_number: The PR number

    Returns:
        Markdown string with all feedback
    """
    checks_dir = get_pr_checks_dir(pr_number)
    meta = load_pr_meta(pr_number)
    if not meta:
        return ""

    results = meta.get("check_results", {})
    feedback_parts = []

    for check_name, result in results.items():
        if result.get("status") in ["failed", "warning"]:
            check_file = checks_dir / f"{check_name}.md"
            if check_file.exists():
                feedback_parts.append(check_file.read_text())

    return "\n\n---\n\n".join(feedback_parts)


def create_fix_task(pr_number: int, feedback: str) -> Path:
    """Create a task to fix issues found by gatekeepers.

    Args:
        pr_number: The PR number
        feedback: Aggregated feedback from gatekeepers

    Returns:
        Path to the created task
    """
    meta = load_pr_meta(pr_number)
    if not meta:
        raise ValueError(f"No metadata found for PR-{pr_number}")

    title = f"Fix gatekeeper issues in PR #{pr_number}"
    branch = meta.get("branch", "unknown")
    pr_title = meta.get("title", "Unknown PR")

    _, failed_checks = all_checks_passed(pr_number)

    context = f"""This PR needs fixes based on gatekeeper feedback.

**PR:** #{pr_number} - {pr_title}
**Branch:** {branch}
**Failed Checks:** {', '.join(failed_checks)}

## Gatekeeper Feedback

{feedback}

## Instructions

1. Check out the PR branch: `git checkout {branch}`
2. Address each issue identified by the gatekeepers
3. Commit your fixes with clear messages
4. Push to update the PR

The PR will be automatically re-checked after you push.
"""

    acceptance_criteria = [
        f"All issues from {check} are resolved" for check in failed_checks
    ]
    acceptance_criteria.append("PR passes all gatekeeper checks on re-run")

    task_path = create_task(
        title=title,
        role="implement",
        context=context,
        acceptance_criteria=acceptance_criteria,
        priority="P1",
        branch=branch,
        created_by="gatekeeper-coordinator",
    )

    # Update PR status
    meta["status"] = "blocked"
    meta["fix_task"] = task_path.stem
    save_pr_meta(pr_number, meta)

    return task_path


def approve_pr_for_review(pr_number: int) -> None:
    """Mark a PR as approved by gatekeepers, ready for human review.

    Args:
        pr_number: The PR number
    """
    meta = load_pr_meta(pr_number)
    if meta:
        meta["status"] = "approved"
        meta["approved_at"] = datetime.now().isoformat()
        save_pr_meta(pr_number, meta)


def add_pr_comment(pr_number: int, body: str) -> bool:
    """Add a comment to a PR.

    Args:
        pr_number: The PR number
        body: Comment body (markdown)

    Returns:
        True if successful
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "comment", str(pr_number), "--body", body],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False


def request_pr_changes(pr_number: int, body: str) -> bool:
    """Request changes on a PR via review.

    Args:
        pr_number: The PR number
        body: Review body explaining required changes

    Returns:
        True if successful
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "review", str(pr_number), "--request-changes", "--body", body],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False


def get_prs_needing_checks() -> list[dict[str, Any]]:
    """Get list of PRs that need gatekeeper checks.

    Returns:
        List of PR metadata dictionaries
    """
    prs_dir = get_prs_dir()
    if not prs_dir.exists():
        return []

    needing_checks = []

    for pr_dir in prs_dir.iterdir():
        if not pr_dir.is_dir() or not pr_dir.name.startswith("PR-"):
            continue

        try:
            pr_number = int(pr_dir.name.replace("PR-", ""))
        except ValueError:
            continue

        meta = load_pr_meta(pr_number)
        if meta and meta.get("status") in ["pending", "running"]:
            needing_checks.append(meta)

    return needing_checks


def get_pr_status_summary() -> dict[str, Any]:
    """Get summary of all PR statuses.

    Returns:
        Dictionary with counts and lists by status
    """
    prs_dir = get_prs_dir()
    if not prs_dir.exists():
        return {"pending": [], "running": [], "approved": [], "blocked": []}

    summary: dict[str, list] = {
        "pending": [],
        "running": [],
        "approved": [],
        "blocked": [],
    }

    for pr_dir in prs_dir.iterdir():
        if not pr_dir.is_dir() or not pr_dir.name.startswith("PR-"):
            continue

        try:
            pr_number = int(pr_dir.name.replace("PR-", ""))
        except ValueError:
            continue

        meta = load_pr_meta(pr_number)
        if meta:
            status = meta.get("status", "pending")
            if status in summary:
                summary[status].append(meta)

    return summary
