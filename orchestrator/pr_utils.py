"""PR interaction utilities."""

import subprocess


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
