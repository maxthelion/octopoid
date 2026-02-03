"""Gatekeeper Coordinator role - orchestrates PR reviews."""

from ..config import get_gatekeeper_config, is_gatekeeper_enabled
from ..pr_utils import (
    add_pr_comment,
    all_checks_complete,
    all_checks_passed,
    approve_pr_for_review,
    create_fix_task,
    detect_new_prs,
    get_check_feedback,
    get_pr_info,
    get_prs_needing_checks,
    init_pr_check,
    load_pr_meta,
    request_pr_changes,
    save_pr_meta,
)
from .base import BaseRole, main_entry


class GatekeeperCoordinatorRole(BaseRole):
    """Coordinator that orchestrates PR gatekeeper checks.

    The coordinator:
    1. Detects new/updated PRs
    2. Initializes check tracking for each PR
    3. Monitors check completion
    4. Creates fix tasks when checks fail
    5. Approves PRs when all checks pass
    """

    def run(self) -> int:
        """Run the gatekeeper coordinator.

        Returns:
            Exit code (0 for success)
        """
        if not is_gatekeeper_enabled():
            self.log("Gatekeeper system is disabled")
            return 0

        gk_config = get_gatekeeper_config()

        # Phase 1: Detect new PRs and initialize tracking
        new_prs = detect_new_prs()
        for pr in new_prs:
            pr_number = pr["number"]
            self.log(f"Initializing checks for PR-{pr_number}: {pr.get('title', '')}")

            # Get full PR info
            pr_info = get_pr_info(pr_number)
            if not pr_info:
                self.log(f"Could not fetch info for PR-{pr_number}")
                continue

            # Initialize PR tracking
            init_pr_check(pr_number, pr_info)

        # Phase 2: Check on PRs that are being processed
        prs_in_progress = get_prs_needing_checks()
        for meta in prs_in_progress:
            pr_number = meta["pr_number"]
            status = meta.get("status", "pending")

            self.log(f"PR-{pr_number} status: {status}")

            # Check if all checks are complete
            if not all_checks_complete(pr_number):
                # Still waiting for checks
                self.log(f"PR-{pr_number}: checks still running")
                continue

            # All checks complete - evaluate results
            passed, failed_checks = all_checks_passed(pr_number)

            if passed:
                self._handle_passed(pr_number, gk_config)
            else:
                self._handle_failed(pr_number, failed_checks)

        return 0

    def _handle_passed(self, pr_number: int, gk_config: dict) -> None:
        """Handle a PR that passed all checks.

        Args:
            pr_number: The PR number
            gk_config: Gatekeeper configuration
        """
        self.log(f"PR-{pr_number}: All checks passed!")

        # Mark as approved
        approve_pr_for_review(pr_number)

        # Add comment to PR
        comment = """## Gatekeeper Review Complete

All automated checks have passed. This PR is ready for human review.

### Checks Completed
"""
        meta = load_pr_meta(pr_number)
        if meta:
            results = meta.get("check_results", {})
            for check_name, result in results.items():
                status = result.get("status", "unknown")
                summary = result.get("summary", "")
                emoji = "✅" if status == "passed" else "⚠️" if status == "warning" else "❓"
                comment += f"- {emoji} **{check_name}**: {summary}\n"

        add_pr_comment(pr_number, comment)

        # Send info message
        self.send_info(
            f"PR #{pr_number} ready for review",
            f"All gatekeeper checks passed. The PR is ready for human review.",
        )

    def _handle_failed(self, pr_number: int, failed_checks: list[str]) -> None:
        """Handle a PR with failed checks.

        Args:
            pr_number: The PR number
            failed_checks: List of check names that failed
        """
        self.log(f"PR-{pr_number}: Failed checks: {', '.join(failed_checks)}")

        # Get aggregated feedback
        feedback = get_check_feedback(pr_number)

        # Create fix task
        task_path = create_fix_task(pr_number, feedback)
        self.log(f"Created fix task: {task_path.stem}")

        # Add comment to PR
        comment = f"""## Gatekeeper Review: Changes Requested

Some automated checks have failed. A task has been created to address the issues.

### Failed Checks
"""
        for check in failed_checks:
            comment += f"- ❌ **{check}**\n"

        comment += f"""
### Next Steps
A fix task (`{task_path.stem}`) has been created with detailed feedback.
The PR will be re-checked once fixes are pushed.
"""

        add_pr_comment(pr_number, comment)

        # Request changes via review
        request_pr_changes(
            pr_number,
            f"Automated checks failed: {', '.join(failed_checks)}. "
            f"See task {task_path.stem} for details.",
        )

        # Send warning message
        self.send_warning(
            f"PR #{pr_number} needs fixes",
            f"Failed checks: {', '.join(failed_checks)}. "
            f"Fix task created: {task_path.stem}",
        )


def main():
    main_entry(GatekeeperCoordinatorRole)


if __name__ == "__main__":
    main()
