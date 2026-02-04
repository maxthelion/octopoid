"""PR Coordinator role - watches for new PRs and creates review tasks."""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from ..queue_utils import get_queue_subdir
from .base import BaseRole, main_entry


class PRCoordinatorRole(BaseRole):
    """PR Coordinator that watches for new PRs and creates review tasks."""

    def get_open_prs(self) -> list[dict[str, Any]]:
        """Get list of open PRs from GitHub.

        Returns:
            List of PR info dictionaries
        """
        try:
            result = subprocess.run(
                [
                    "gh", "pr", "list",
                    "--state", "open",
                    "--json", "number,headRefName,title,createdAt"
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                self.log(f"Failed to list PRs: {result.stderr}")
                return []

            return json.loads(result.stdout)

        except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError) as e:
            self.log(f"Error getting PRs: {e}")
            return []

    def is_agent_branch(self, branch_name: str) -> bool:
        """Check if a branch is an agent-created branch.

        Args:
            branch_name: The branch name to check

        Returns:
            True if the branch was created by an agent
        """
        return branch_name.startswith("agent/")

    def pr_has_review_task(self, pr_number: int) -> bool:
        """Check if a review task exists for this PR.

        Checks incoming, claimed, and done queues for existing tasks.

        Args:
            pr_number: The PR number to check

        Returns:
            True if a review task already exists
        """
        task_filename = f"TASK-review-pr{pr_number}.md"

        for queue in ["incoming", "claimed", "done"]:
            queue_dir = get_queue_subdir(queue)
            if (queue_dir / task_filename).exists():
                return True

        return False

    def get_repo_info(self) -> tuple[str, str]:
        """Get the owner and repo name from git remote.

        Returns:
            Tuple of (owner, repo)
        """
        try:
            result = subprocess.run(
                ["gh", "repo", "view", "--json", "owner,name"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                return data.get("owner", {}).get("login", "unknown"), data.get("name", "unknown")

        except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError):
            pass

        return "unknown", "unknown"

    def create_review_task(self, pr: dict[str, Any]) -> Path | None:
        """Create a review task for a PR.

        Args:
            pr: PR info dictionary with number, title, headRefName

        Returns:
            Path to created task file, or None on failure
        """
        pr_number = pr["number"]
        pr_title = pr["title"]
        branch = pr["headRefName"]

        owner, repo = self.get_repo_info()
        pr_url = f"https://github.com/{owner}/{repo}/pull/{pr_number}"

        task_filename = f"TASK-review-pr{pr_number}.md"
        timestamp = datetime.now().isoformat()

        content = f"""# [TASK-review-pr{pr_number}] Review PR #{pr_number}: {pr_title}

ROLE: review
PRIORITY: P1
BRANCH: main
CREATED: {timestamp}
CREATED_BY: pr_coordinator

## Context

Review the implementation in PR #{pr_number}.

PR: {pr_url}
Branch: {branch}

## Instructions

1. Use `gh pr diff {pr_number}` to see the changes
2. Review for code quality, correctness, and test coverage
3. Use `gh pr review {pr_number}` to approve or request changes

## Acceptance Criteria

- [ ] Code reviewed for bugs and logic errors
- [ ] Security implications considered
- [ ] Test coverage adequate
- [ ] Review submitted via GitHub
"""

        incoming_dir = get_queue_subdir("incoming")
        task_path = incoming_dir / task_filename

        try:
            task_path.write_text(content)
            return task_path
        except OSError as e:
            self.log(f"Failed to create task file: {e}")
            return None

    def run(self) -> int:
        """Check for unreviewed PRs and create review tasks.

        Returns:
            Exit code (0 for success)
        """
        self.log("Checking for PRs needing review...")

        # Get all open PRs
        prs = self.get_open_prs()
        if not prs:
            self.log("No open PRs found")
            return 0

        self.log(f"Found {len(prs)} open PRs")

        # Filter to agent PRs without review tasks
        tasks_created = 0
        for pr in prs:
            pr_number = pr["number"]
            branch = pr["headRefName"]
            title = pr["title"]

            # Only process agent-created PRs
            if not self.is_agent_branch(branch):
                self.debug_log(f"PR #{pr_number}: Skipping (not an agent branch: {branch})")
                continue

            # Skip if already has a review task
            if self.pr_has_review_task(pr_number):
                self.debug_log(f"PR #{pr_number}: Skipping (review task exists)")
                continue

            # Create review task
            self.log(f"Creating review task for PR #{pr_number}: {title}")
            task_path = self.create_review_task(pr)

            if task_path:
                self.log(f"Created: {task_path.name}")
                tasks_created += 1
            else:
                self.log(f"Failed to create task for PR #{pr_number}")

        if tasks_created > 0:
            self.log(f"Created {tasks_created} review task(s)")
        else:
            self.log("No new review tasks needed")

        return 0


def main():
    main_entry(PRCoordinatorRole)


if __name__ == "__main__":
    main()
