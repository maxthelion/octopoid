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
    """Breakdown agent that decomposes work into right-sized tasks.

    Uses a two-phase approach:
    1. Exploration phase: Investigate codebase with tools to understand patterns
    2. Decomposition phase: Output structured JSON based on findings
    """

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
            task_content = task.get("content", "")

            project_context = ""
            if project:
                project_context = f"""
Project: {project['id']}
Title: {project['title']}
Branch: {project.get('branch', 'N/A')}
"""

            # ===== PHASE 1: EXPLORATION =====
            self.log("Phase 1: Exploring codebase...")

            exploration_prompt = f"""You are analyzing a codebase to plan implementation tasks.

## Work to Implement

{task_content}

{project_context}

## Your Task

Explore the codebase to understand:

1. **Testing patterns**: How are tests structured? Find example test files. Note the testing framework and patterns used (describe/it, test fixtures, etc.)

2. **Relevant files**: Which files will need to be modified? List specific file paths.

3. **Existing patterns**: Find similar existing code that this work should follow. Note any conventions.

4. **Integration points**: Where does this feature connect to existing systems?

## Output Format

Output your findings as markdown with these sections:

### Testing Approach
- Testing framework used
- Example test file to follow
- Test patterns to use

### Files to Modify
- List specific file paths
- Brief note on what changes each file needs

### Patterns to Follow
- Existing similar code to reference
- Conventions observed

### Integration Points
- How this connects to existing code
- Dependencies to be aware of

Be specific - include file paths, line numbers where helpful, and concrete examples.
"""

            exploration_tools = ["Read", "Glob", "Grep", "Task"]

            exit_code, exploration_output, stderr = self.invoke_claude(
                exploration_prompt,
                allowed_tools=exploration_tools,
                max_turns=15,
            )

            if exit_code != 0:
                self.log(f"Exploration phase failed: {stderr}")
                fail_task(task_path, f"Exploration failed: {stderr[:500]}")
                return exit_code

            self.log("Phase 1 complete. Findings captured.")

            # ===== PHASE 2: DECOMPOSITION =====
            self.log("Phase 2: Generating task breakdown...")

            decomposition_prompt = f"""Based on the exploration findings below, create a task breakdown.

## Original Requirements

{task_content}

## Exploration Findings

{exploration_output}

## Breakdown Rules

{rules}

## Output Format

Output ONLY a JSON array. Each task should include specific file paths and follow patterns identified in exploration.

```json
[
  {{
    "title": "Short task title",
    "role": "implement",
    "priority": "P1",
    "context": "Specific instructions including file paths to modify. Reference patterns found.",
    "acceptance_criteria": ["Specific, checkable criterion"],
    "depends_on": []
  }}
]
```

Rules:
- Testing strategy task FIRST (depends_on: [])
- Reference specific files discovered in exploration
- Include test file paths in testing tasks
- depends_on uses 1-indexed task numbers
- 4-8 tasks total

Output ONLY the JSON array:
"""

            # No tools for decomposition - just structured output
            exit_code, decomposition_output, stderr = self.invoke_claude(
                decomposition_prompt,
                allowed_tools=[],
                max_turns=5,
            )

            if exit_code != 0:
                self.log(f"Decomposition phase failed: {stderr}")
                fail_task(task_path, f"Decomposition failed: {stderr[:500]}")
                return exit_code

            # Parse the JSON output
            subtasks = self._parse_subtasks(decomposition_output)

            if not subtasks:
                self.log("Failed to parse subtasks from output")
                fail_task(task_path, "Could not parse subtask JSON from Claude output")
                return 1

            self.log(f"Parsed {len(subtasks)} subtasks")

            # Write breakdown file for human review
            breakdown_file = self._write_breakdown_file(
                subtasks=subtasks,
                project_id=project_id,
                project=project,
                task_title=task_title,
                branch=task.get("branch"),
                exploration_findings=exploration_output,
            )

            # Complete the breakdown task
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
        return """### Sizing
- Tasks should be completable in <30 Claude turns
- If unsure, err toward smaller tasks
- One clear objective per task

### Ordering
1. Testing strategy task FIRST
2. Schema/type changes early
3. Core logic before UI wiring
4. Integration tests last

### Task Context Should Include
- Specific file paths to modify
- Line numbers or function names when known
- Patterns to follow (reference exploration findings)
- Test file locations for test tasks
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
        exploration_findings: str = "",
    ) -> Path:
        """Write breakdown to a markdown file for human review.

        Args:
            subtasks: List of subtask definitions from Claude
            project_id: Parent project ID if applicable
            project: Project dict if applicable
            task_title: Original breakdown task title
            branch: Branch to use for tasks
            exploration_findings: Output from exploration phase

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

        # Exploration findings (collapsible)
        if exploration_findings:
            lines.append("<details>")
            lines.append("<summary><strong>Exploration Findings</strong> (click to expand)</summary>")
            lines.append("")
            lines.append(exploration_findings)
            lines.append("")
            lines.append("</details>")
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
        lines.append("2. Check that file paths and patterns are correct")
        lines.append("3. Edit any tasks as needed (adjust criteria, context, dependencies)")
        lines.append("4. Remove tasks that aren't needed")
        lines.append("5. Add any missing tasks")
        lines.append(f"6. When ready, run: `/approve-breakdown {project_id or breakdown_path.stem}`")
        lines.append("")

        # Write file
        breakdown_path.write_text("\n".join(lines))

        return breakdown_path


if __name__ == "__main__":
    main_entry(BreakdownRole)
