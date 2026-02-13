"""Render prompt templates for script-based agents."""

from pathlib import Path
from string import Template
from typing import Any

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def render_prompt(
    role: str,
    task: dict[str, Any],
    *,
    global_instructions: str = "",
    review_feedback: str = "",
    continuation_notes: str = "",
    scripts_dir: str = "../scripts",
    agent_hooks: list[dict] | None = None,
) -> str:
    """Render a prompt template for a script-based agent."""
    template_path = _PROMPTS_DIR / f"{role}.md"
    if not template_path.exists():
        raise FileNotFoundError(
            f"No prompt template for role '{role}' at {template_path}"
        )

    template_text = template_path.read_text()

    required_steps = ""
    if agent_hooks:
        lines = ["## Required Steps Before Finishing", ""]
        lines.append("You must complete these steps before calling finish:")
        for i, hook in enumerate(agent_hooks, 1):
            name = hook["name"]
            if name == "run_tests":
                lines.append(f"{i}. Run tests: `{scripts_dir}/run-tests`")
            elif name == "create_pr":
                lines.append(f"{i}. Submit PR: `{scripts_dir}/submit-pr`")
            elif name == "rebase_on_main":
                lines.append(
                    f"{i}. Rebase on main: "
                    "`git fetch origin main && git rebase origin/main`"
                )
            else:
                lines.append(f"{i}. {name}")
        required_steps = "\n".join(lines)

    review_section = ""
    if review_feedback:
        review_section = (
            "## Previous Review Feedback\n\n"
            "This task was previously rejected. "
            "Address the following feedback:\n\n"
            f"{review_feedback}"
        )

    continuation_section = ""
    if continuation_notes:
        continuation_section = (
            "## Continuation Notes\n\n"
            "This task was partially completed in a previous run. "
            "Review the notes below and continue from where "
            "the previous agent left off:\n\n"
            f"{continuation_notes}"
        )

    template = Template(template_text)
    return template.safe_substitute(
        task_id=task.get("id", "unknown"),
        task_title=task.get("title", "Untitled"),
        task_content=task.get("content", ""),
        task_priority=task.get("priority", "P2"),
        task_branch=task.get("branch", "main"),
        task_type=task.get("type", ""),
        scripts_dir=scripts_dir,
        global_instructions=global_instructions,
        required_steps=required_steps,
        review_section=review_section,
        continuation_section=continuation_section,
    )
