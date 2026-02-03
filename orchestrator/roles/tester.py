"""Tester role - runs tests and adds test coverage."""

import sys

from ..queue_utils import can_claim_task, claim_task, complete_task, fail_task
from .base import BaseRole, main_entry


class TesterRole(BaseRole):
    """Tester that runs tests and improves test coverage."""

    def run(self) -> int:
        """Claim a test task and execute it.

        Returns:
            Exit code (0 for success)
        """
        # Check backpressure
        can_claim, reason = can_claim_task()
        if not can_claim:
            self.log(f"Cannot claim task: {reason}")
            return 0

        # Try to claim a test task
        task = claim_task(role_filter="test", agent_name=self.agent_name)
        if not task:
            self.log("No test tasks available")
            return 0

        task_id = task["id"]
        task_title = task["title"]
        task_path = task["path"]

        self.log(f"Claimed test task {task_id}: {task_title}")

        try:
            # Build prompt for Claude
            instructions = self.read_instructions()
            task_content = task.get("content", "")

            prompt = f"""You are a tester agent working on this testing task.

{instructions}

## Task Details

{task_content}

## Instructions

1. Understand what needs to be tested
2. Run existing tests to establish baseline
3. Add new tests as specified in the task
4. Ensure all tests pass
5. Report test results and coverage

Use the /test skill for guidance on testing best practices.

Remember:
- You can read all files
- You can only modify test files
- Do not change production code
- Document any issues found
"""

            # Invoke Claude with testing tools
            allowed_tools = [
                "Read",
                "Write",  # For test files only
                "Edit",   # For test files only
                "Glob",
                "Grep",
                "Bash",   # For running tests
                "Skill",
            ]

            exit_code, stdout, stderr = self.invoke_claude(
                prompt,
                allowed_tools=allowed_tools,
                max_turns=30,
            )

            if exit_code != 0:
                self.log(f"Testing failed: {stderr}")
                fail_task(task_path, f"Testing failed with exit code {exit_code}\n{stderr}")
                return exit_code

            # Complete the task with results
            complete_task(task_path, f"Testing complete.\n\n{stdout[-2000:]}")
            self.log("Testing task complete")
            return 0

        except Exception as e:
            self.log(f"Test task failed: {e}")
            fail_task(task_path, str(e))
            return 1


def main():
    main_entry(TesterRole)


if __name__ == "__main__":
    main()
