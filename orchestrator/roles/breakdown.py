"""Breakdown role - decomposes projects and large tasks into implementation tasks."""

import json
import re
from pathlib import Path

from ..config import is_db_enabled
from ..queue_utils import (
    claim_task,
    complete_task,
    create_task,
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
        task = claim_task(role_filter="breakdown", agent_name=self.agent_name)
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

            # Create the tasks
            created_ids = self._create_subtasks(subtasks, project_id, task.get("branch"))

            if not created_ids:
                fail_task(task_path, "Failed to create any subtasks")
                return 1

            # Complete the breakdown task
            complete_task(task_path, f"Decomposed into {len(created_ids)} tasks: {', '.join(created_ids)}")
            self.log(f"Created {len(created_ids)} tasks: {created_ids}")

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

    def _create_subtasks(
        self,
        subtasks: list[dict],
        project_id: str | None,
        branch: str | None,
    ) -> list[str]:
        """Create subtasks in the incoming queue.

        Args:
            subtasks: List of subtask definitions
            project_id: Parent project ID if applicable
            branch: Branch to use for tasks

        Returns:
            List of created task IDs
        """
        created_ids = []
        id_map = {}  # Map from 1-indexed position to actual task ID

        for i, subtask in enumerate(subtasks, start=1):
            title = subtask.get("title", f"Subtask {i}")
            role = subtask.get("role", "implement")
            priority = subtask.get("priority", "P2")
            context = subtask.get("context", "")
            criteria = subtask.get("acceptance_criteria", [])
            depends_on = subtask.get("depends_on", [])

            # Resolve dependencies to actual task IDs
            blocked_by = None
            if depends_on:
                blocker_ids = []
                for dep_num in depends_on:
                    if dep_num in id_map:
                        blocker_ids.append(id_map[dep_num])
                if blocker_ids:
                    blocked_by = ",".join(blocker_ids)

            try:
                task_path = create_task(
                    title=title,
                    role=role,
                    context=context,
                    acceptance_criteria=criteria if criteria else ["Complete the task"],
                    priority=priority,
                    branch=branch or "main",
                    created_by=self.agent_name,
                    blocked_by=blocked_by,
                    project_id=project_id,
                    queue="incoming",  # Created tasks go to incoming
                )

                # Extract task ID from path
                task_id = task_path.stem.replace("TASK-", "")
                id_map[i] = task_id
                created_ids.append(task_id)

                self.log(f"  Created task {task_id}: {title}")

            except Exception as e:
                self.log(f"  Failed to create task '{title}': {e}")

        return created_ids


if __name__ == "__main__":
    main_entry(BreakdownRole)
