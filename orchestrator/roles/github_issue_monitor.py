"""GitHub issue monitor role - polls GitHub issues and creates tasks for new ones."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..queue_utils import can_create_task, create_task
from .base import BaseRole, main_entry


class GitHubIssueMonitorRole(BaseRole):
    """Monitor GitHub issues and create tasks for new ones.

    This agent:
    - Polls GitHub issues using the gh CLI
    - Tracks which issues have been processed
    - Creates tasks for new issues
    - Uses issue number to avoid duplicates
    """

    def __init__(self):
        """Initialize the GitHub issue monitor."""
        super().__init__()
        self.state_file = self.orchestrator_dir / "runtime" / "github_issues_state.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> dict[str, Any]:
        """Load the state of processed issues.

        Returns:
            Dictionary with 'processed_issues' list of issue numbers
        """
        if not self.state_file.exists():
            return {"processed_issues": []}

        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            self.log(f"Error loading state file: {e}, starting fresh")
            return {"processed_issues": []}

    def save_state(self, state: dict[str, Any]) -> None:
        """Save the state of processed issues.

        Args:
            state: Dictionary with 'processed_issues' list
        """
        try:
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
        except OSError as e:
            self.log(f"Error saving state file: {e}")

    def fetch_github_issues(self) -> list[dict[str, Any]]:
        """Fetch open GitHub issues using the gh CLI.

        Returns:
            List of issue dictionaries with keys: number, title, url, body, labels
        """
        try:
            # Use gh CLI to fetch issues
            result = subprocess.run(
                [
                    "gh", "issue", "list",
                    "--state", "open",
                    "--json", "number,title,url,body,labels",
                    "--limit", "100"
                ],
                cwd=self.parent_project,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                self.log(f"Error fetching issues: {result.stderr}")
                return []

            issues = json.loads(result.stdout)
            return issues

        except subprocess.TimeoutExpired:
            self.log("Timeout fetching GitHub issues")
            return []
        except json.JSONDecodeError as e:
            self.log(f"Error parsing GitHub issues JSON: {e}")
            return []
        except FileNotFoundError:
            self.log("gh CLI not found - please install GitHub CLI (gh)")
            return []
        except Exception as e:
            self.log(f"Unexpected error fetching issues: {e}")
            return []

    def create_task_from_issue(self, issue: dict[str, Any]) -> bool:
        """Create a task from a GitHub issue.

        Args:
            issue: Issue dictionary with number, title, url, body, labels

        Returns:
            True if task was created successfully
        """
        issue_number = issue["number"]
        title = issue["title"]
        url = issue["url"]
        body = issue.get("body", "")
        labels = [label["name"] for label in issue.get("labels", [])]

        # Determine priority from labels
        priority = "P1"  # default
        if any(label in ["urgent", "critical", "P0"] for label in labels):
            priority = "P0"
        elif any(label in ["low-priority", "P2"] for label in labels):
            priority = "P2"

        # Determine role from labels
        role = "implement"  # default
        if any(label in ["bug", "fix"] for label in labels):
            role = "implement"
        elif any(label in ["documentation", "docs"] for label in labels):
            role = "implement"
        elif any(label in ["enhancement", "feature"] for label in labels):
            role = "implement"

        # Build context
        context_parts = [
            f"**GitHub Issue:** [{issue_number}]({url})",
            "",
            "**Description:**",
            body if body else "(No description provided)",
        ]

        if labels:
            context_parts.extend([
                "",
                "**Labels:** " + ", ".join(labels),
            ])

        context = "\n".join(context_parts)

        # Build acceptance criteria
        acceptance_criteria = [
            f"Resolve GitHub issue #{issue_number}",
            "All tests pass",
            "Code follows project conventions",
        ]

        # Check if we can create the task
        if not can_create_task():
            self.log(f"Cannot create task for issue #{issue_number} - queue limit reached")
            return False

        try:
            # Create the task
            task_path = create_task(
                title=f"[GH-{issue_number}] {title}",
                role=role,
                context=context,
                acceptance_criteria=acceptance_criteria,
                priority=priority,
                created_by=self.agent_name,
                queue="incoming",
            )

            self.log(f"Created task for issue #{issue_number}: {task_path.name}")

            # Add a comment to the issue noting that a task was created
            self._comment_on_issue(issue_number, task_path.name)

            return True

        except Exception as e:
            self.log(f"Error creating task for issue #{issue_number}: {e}")
            return False

    def _comment_on_issue(self, issue_number: int, task_id: str) -> None:
        """Add a comment to the GitHub issue noting that a task was created.

        Args:
            issue_number: GitHub issue number
            task_id: Created task ID
        """
        try:
            comment = (
                f"🤖 Octopoid has automatically created task `{task_id}` for this issue.\n\n"
                f"The task is now in the queue and will be picked up by an available agent."
            )

            subprocess.run(
                [
                    "gh", "issue", "comment", str(issue_number),
                    "--body", comment
                ],
                cwd=self.parent_project,
                capture_output=True,
                timeout=10,
            )
        except Exception as e:
            # Don't fail if we can't comment - the task is still created
            self.debug_log(f"Could not comment on issue #{issue_number}: {e}")

    def run(self) -> int:
        """Execute the GitHub issue monitor.

        Returns:
            Exit code (0 for success)
        """
        self.log("Starting GitHub issue monitor")

        # Load state
        state = self.load_state()
        processed_issues = set(state.get("processed_issues", []))

        self.debug_log(f"Loaded state: {len(processed_issues)} processed issues")

        # Fetch current issues
        issues = self.fetch_github_issues()

        if not issues:
            self.debug_log("No issues found or error fetching issues")
            return 0

        self.log(f"Found {len(issues)} open issues")

        # Process new issues
        new_issues_count = 0
        for issue in issues:
            issue_number = issue["number"]

            if issue_number in processed_issues:
                self.debug_log(f"Skipping already processed issue #{issue_number}")
                continue

            self.log(f"Processing new issue #{issue_number}: {issue['title']}")

            if self.create_task_from_issue(issue):
                processed_issues.add(issue_number)
                new_issues_count += 1
            else:
                self.log(f"Failed to create task for issue #{issue_number}")

        # Save updated state
        state["processed_issues"] = sorted(list(processed_issues))
        self.save_state(state)

        if new_issues_count > 0:
            self.log(f"Created {new_issues_count} new tasks from GitHub issues")
        else:
            self.log("No new issues to process")

        return 0


if __name__ == "__main__":
    main_entry(GitHubIssueMonitorRole)
