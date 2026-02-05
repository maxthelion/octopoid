"""Breakdown role - decomposes projects and large tasks into implementation tasks."""

import json
import re
from datetime import datetime
from pathlib import Path

from ..config import is_db_enabled, get_orchestrator_dir
from ..queue_utils import (
    claim_task,
    complete_task,
    fail_task,
    get_project,
)
from .base import BaseRole, main_entry


class BreakdownRole(BaseRole):
    """Breakdown agent that decomposes work into right-sized tasks."""

    def run(self) -> int:
        """Claim a breakdown task and decompose it.

        Returns:
            Exit code (0 for success)
        """
        if not is_db_enabled():
            self.log("Breakdown role requires database mode")
            return 1

        # Claim from breakdown queue
        task = claim_task(role_filter="breakdown", agent_name=self.agent_name, from_queue="breakdown")
        if not task:
            self.log("No tasks in breakdown queue")
            return 0

        task_id = task["id"]
        task_title = task["title"]
        task_path = task["path"]
        project_id = task.get("project_id")

        self.current_task_id = task_id
        self.log(f"Claimed breakdown task {task_id}: {task_title}")

        # Get project info if this is part of a project
        project = None
        if project_id:
            project = get_project(project_id)
            self.log(f"Project: {project_id} - {project.get('title') if project else 'Unknown'}")

        try:
            # Load breakdown rules from prompts
            rules = self._load_breakdown_rules()

            # Build prompt for Claude
            instructions = self.read_instructions()
            task_content = task.get("content", "")

            project_context = ""
            if project:
                project_context = f"""
## Project Context

This breakdown is for project: {project['id']}
Title: {project['title']}
Description: {project.get('description', 'N/A')}
Branch: {project.get('branch', 'N/A')}

All tasks created should be linked to this project.
"""

            prompt = f"""You are a breakdown agent. Your job is to decompose work into right-sized implementation tasks.

{instructions}

{rules}

{project_context}

## Work to Break Down

{task_content}

## Instructions

1. Analyze the work described above
2. Break it down into discrete, implementable tasks
3. Output your breakdown in the JSON format specified below
4. Each task should be completable in <30 Claude turns
5. Always include a testing strategy task FIRST
6. Map dependencies using task numbers (e.g., task 2 depends on task 1)

## Output Format

Output ONLY a JSON array of tasks, nothing else:

```json
[
  {{
    "title": "Define testing strategy for feature",
    "role": "implement",
    "priority": "P1",
    "context": "Detailed description of what to test and how...",
    "acceptance_criteria": [
      "Unit tests for core functions",
      "Integration test for workflow"
    ],
    "depends_on": []
  }},
  {{
    "title": "Implement core data schema",
    "role": "implement",
    "priority": "P1",
    "context": "Description of schema changes...",
    "acceptance_criteria": [
      "Types defined",
      "Validation added"
    ],
    "depends_on": [1]
  }}
]
```

Remember:
- Task numbers in depends_on are 1-indexed (first task is 1)
- Testing strategy should be task 1 with no dependencies
- Keep tasks focused and atomic
- Include clear acceptance criteria
"""

            # Invoke Claude with limited tools (read-only exploration)
            allowed_tools = [
                "Read",
                "Glob",
                "Grep",
                "Task",
            ]

            exit_code, stdout, stderr = self.invoke_claude(
                prompt,
                allowed_tools=allowed_tools,
                max_turns=20,
            )

            if exit_code != 0:
                self.log(f"Breakdown failed: {stderr}")
                fail_task(task_path, f"Claude invocation failed: {stderr[:500]}")
                return exit_code

            # Parse the JSON output
            subtasks = self._parse_subtasks(stdout)

            if not subtasks:
                self.log("Failed to parse subtasks from output")
                fail_task(task_path, "Could not parse subtask JSON from Claude output")
                return 1

            self.log(f"Parsed {len(subtasks)} subtasks")

            # Write breakdown file for human review (instead of creating tasks directly)
            breakdown_file = self._write_breakdown_file(
                subtasks=subtasks,
                project_id=project_id,
                project=project,
                task_title=task_title,
                branch=task.get("branch"),
            )

            # Complete the breakdown task with reference to the breakdown file
            complete_task(
                task_path,
                f"Breakdown ready for review: {breakdown_file.name}\n"
                f"Review with: /approve-breakdown {project_id or breakdown_file.stem}"
            )
            self.log(f"Wrote breakdown file: {breakdown_file}")

            return 0

        except Exception as e:
            self.log(f"Error during breakdown: {e}")
            fail_task(task_path, str(e))
            return 1

    def _load_breakdown_rules(self) -> str:
        """Load breakdown rules from prompts directory.

        Returns:
            Breakdown rules markdown or default rules
        """
        prompts_dir = self.orchestrator_dir / "prompts"
        rules_path = prompts_dir / "breakdown.md"

        if rules_path.exists():
            return rules_path.read_text()

        # Default rules if file doesn't exist
        return """## Task Breakdown Rules

### Sizing
- Tasks should be completable in <30 Claude turns
- If unsure, err toward smaller tasks
- One clear objective per task

### Ordering
1. Testing strategy task FIRST (what to test, how)
2. Schema/type changes early (others depend on them)
3. Core logic before UI wiring
4. Integration tests after implementation

### Dependencies
- Use depends_on to specify which tasks must complete first
- Minimize dependency chains (parallelize where possible)
- Shared utilities should be scheduled first

### Acceptance Criteria
- Each task gets clear, checkable criteria
- Include specific requirements, not vague goals
- Flag tasks needing human input
"""

    def _parse_subtasks(self, output: str) -> list[dict]:
        """Parse subtasks from Claude's JSON output.

        Args:
            output: Claude's output text

        Returns:
            List of subtask dictionaries
        """
        # Try to find JSON array in the output
        # Look for ```json ... ``` blocks first
        json_match = re.search(r'```json\s*\n?(.*?)\n?```', output, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find raw JSON array
            json_match = re.search(r'\[\s*\{.*?\}\s*\]', output, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            else:
                return []

        try:
            subtasks = json.loads(json_str)
            if isinstance(subtasks, list):
                return subtasks
        except json.JSONDecodeError as e:
            self.log(f"JSON parse error: {e}")

        return []

    def _write_breakdown_file(
        self,
        subtasks: list[dict],
        project_id: str | None,
        project: dict | None,
        task_title: str,
        branch: str | None,
    ) -> Path:
        """Write breakdown to a markdown file for human review.

        Args:
            subtasks: List of subtask definitions from Claude
            project_id: Parent project ID if applicable
            project: Project dict if applicable
            task_title: Original breakdown task title
            branch: Branch to use for tasks

        Returns:
            Path to the created breakdown file
        """
        # Ensure breakdowns directory exists
        breakdowns_dir = get_orchestrator_dir() / "shared" / "breakdowns"
        breakdowns_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename
        if project_id:
            filename = f"{project_id}-breakdown.md"
        else:
            # Use timestamp for non-project breakdowns
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"breakdown-{timestamp}.md"

        breakdown_path = breakdowns_dir / filename

        # Build markdown content
        lines = []

        # Header
        title = project.get("title") if project else task_title.replace("Break down: ", "")
        lines.append(f"# Breakdown: {title}")
        lines.append("")

        # Metadata
        if project_id:
            lines.append(f"**Project:** {project_id}")
        if branch:
            lines.append(f"**Branch:** {branch}")
        lines.append(f"**Created:** {datetime.now().isoformat()}")
        lines.append(f"**Status:** pending_review")
        lines.append("")

        # Tasks
        for i, subtask in enumerate(subtasks, start=1):
            lines.append("---")
            lines.append("")
            lines.append(f"## Task {i}: {subtask.get('title', f'Subtask {i}')}")
            lines.append("")

            # Task metadata
            role = subtask.get("role", "implement")
            priority = subtask.get("priority", "P2")
            depends_on = subtask.get("depends_on", [])

            lines.append(f"**Role:** {role}")
            lines.append(f"**Priority:** {priority}")

            if depends_on:
                deps_str = ", ".join(str(d) for d in depends_on)
                lines.append(f"**Depends on:** {deps_str}")
            else:
                lines.append("**Depends on:** (none)")

            lines.append("")

            # Context
            context = subtask.get("context", "")
            if context:
                lines.append("### Context")
                lines.append("")
                lines.append(context)
                lines.append("")

            # Acceptance criteria
            criteria = subtask.get("acceptance_criteria", [])
            if criteria:
                lines.append("### Acceptance Criteria")
                lines.append("")
                for criterion in criteria:
                    lines.append(f"- [ ] {criterion}")
                lines.append("")

        # Footer with instructions
        lines.append("---")
        lines.append("")
        lines.append("## Review Instructions")
        lines.append("")
        lines.append("1. Review the tasks above for completeness and accuracy")
        lines.append("2. Edit any tasks as needed (adjust criteria, context, dependencies)")
        lines.append("3. Remove tasks that aren't needed")
        lines.append("4. Add any missing tasks")
        lines.append(f"5. When ready, run: `/approve-breakdown {project_id or breakdown_path.stem}`")
        lines.append("")

        # Write file
        breakdown_path.write_text("\n".join(lines))

        return breakdown_path


if __name__ == "__main__":
    main_entry(BreakdownRole)
