"""Product Manager role - analyzes repo and creates tasks."""

import sys

from ..queue_utils import can_create_task, create_task
from .base import BaseRole, main_entry


class ProductManagerRole(BaseRole):
    """Product manager that analyzes the repository and creates tasks."""

    def run(self) -> int:
        """Analyze repository and create tasks if backpressure allows.

        Returns:
            Exit code (0 for success)
        """
        # Check backpressure
        can_create, reason = can_create_task()
        if not can_create:
            self.log(f"Backpressure: {reason}")
            return 0  # Not an error, just nothing to do

        # Build prompt for Claude
        instructions = self.read_instructions()

        prompt = f"""You are a product manager agent analyzing this codebase.

{instructions}

Your task is to identify ONE high-value task that should be implemented.

Consider:
1. Code quality issues (missing tests, unclear code, potential bugs)
2. Documentation gaps
3. Performance improvements
4. Security concerns
5. Feature opportunities based on TODO comments or incomplete implementations

Analyze the codebase and then use the /create-task command to create a well-formed task.

The task should be:
- Specific and actionable
- Appropriately scoped (can be completed in one session)
- Valuable to the project

If there are no obvious improvements needed, you may skip creating a task.

Start by exploring the codebase structure, then decide on a task.
"""

        # Invoke Claude with appropriate tools
        allowed_tools = [
            "Read",
            "Glob",
            "Grep",
            "Bash",  # For git commands
            "Skill",  # For /create-task
        ]

        exit_code, stdout, stderr = self.invoke_claude(
            prompt,
            allowed_tools=allowed_tools,
            max_turns=20,
        )

        if exit_code != 0:
            self.log(f"Claude invocation failed: {stderr}")
            return exit_code

        self.log("Task analysis complete")
        return 0


def main():
    main_entry(ProductManagerRole)


if __name__ == "__main__":
    main()
