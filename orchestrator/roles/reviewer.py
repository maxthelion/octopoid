"""Reviewer role - reviews code and provides feedback."""

import sys

from ..queue_utils import can_claim_task, claim_task, complete_task, fail_task
from .base import BaseRole, main_entry


class ReviewerRole(BaseRole):
    """Reviewer that reviews code and provides feedback."""

    def run(self) -> int:
        """Claim a review task and perform code review.

        Returns:
            Exit code (0 for success)
        """
        # Check backpressure
        can_claim, reason = can_claim_task()
        if not can_claim:
            self.log(f"Cannot claim task: {reason}")
            return 0

        # Try to claim a review task
        task = claim_task(role_filter="review", agent_name=self.agent_name)
        if not task:
            self.log("No review tasks available")
            return 0

        task_id = task["id"]
        task_title = task["title"]
        task_path = task["path"]

        self.log(f"Claimed review task {task_id}: {task_title}")

        try:
            # Build prompt for Claude
            instructions = self.read_instructions()
            task_content = task.get("content", "")

            prompt = f"""You are a code reviewer agent.

{instructions}

## Task Details

{task_content}

## Instructions

1. Read and understand the code to be reviewed
2. Check for:
   - Bugs and logic errors
   - Security vulnerabilities (OWASP Top 10)
   - Code style and consistency
   - Performance issues
   - Missing error handling
   - Test coverage gaps
3. Provide constructive feedback
4. If reviewing a PR, use gh CLI to leave review comments

Use the /review skill for guidance on review best practices.

Remember:
- You are in READ-ONLY mode
- Do not modify any code
- Focus on helpful, actionable feedback
- Be constructive, not just critical
"""

            # Invoke Claude with read-only tools
            allowed_tools = [
                "Read",
                "Glob",
                "Grep",
                "Bash",  # For git/gh commands only
                "Skill",
            ]

            exit_code, stdout, stderr = self.invoke_claude(
                prompt,
                allowed_tools=allowed_tools,
                max_turns=20,
            )

            if exit_code != 0:
                self.log(f"Review failed: {stderr}")
                fail_task(task_path, f"Review failed with exit code {exit_code}\n{stderr}")
                return exit_code

            # Complete the task with review results
            complete_task(task_path, f"Review complete.\n\n{stdout[-2000:]}")
            self.log("Review task complete")
            return 0

        except Exception as e:
            self.log(f"Review task failed: {e}")
            fail_task(task_path, str(e))
            return 1


def main():
    main_entry(ReviewerRole)


if __name__ == "__main__":
    main()
