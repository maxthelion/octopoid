"""Prompt rendering utilities for scheduler agents."""

import logging
from pathlib import Path

from .config import get_base_branch, get_global_instructions_path, get_tasks_dir
from . import queue_utils

logger = logging.getLogger("octopoid.scheduler")


def _load_global_instructions(agent_dir: str) -> str:
    """Load global + agent-specific instructions.

    Reads the global instructions file and appends any agent-specific
    instructions.md found in the agent directory.

    Args:
        agent_dir: Path to the agent directory.

    Returns:
        Combined instructions string.
    """
    global_instructions = ""
    gi_path = get_global_instructions_path()
    if gi_path.exists():
        global_instructions = gi_path.read_text()

    instructions_md_path = Path(agent_dir) / "instructions.md"
    if instructions_md_path.exists():
        instructions_content = instructions_md_path.read_text()
        global_instructions = global_instructions + "\n\n" + instructions_content
        logger.debug(f"Included instructions.md from agent directory: {instructions_md_path}")

    return global_instructions


def _parse_agent_hooks(task: dict) -> list[dict]:
    """Parse agent hooks from the task dict.

    Args:
        task: Task dict, may contain a 'hooks' key (JSON string or list).

    Returns:
        List of hook dicts with type == "agent".
    """
    import json as _json

    hooks = task.get("hooks")
    if not hooks:
        return []
    if isinstance(hooks, str):
        raw = _json.loads(hooks)
    elif isinstance(hooks, list):
        raw = hooks
    else:
        return []
    return [h for h in raw if h.get("type") == "agent"]


def _build_required_steps(task: dict) -> str:
    """Build the required-steps section from agent hooks.

    The 'create_pr' hook is intentionally skipped (handled by the scheduler).

    Args:
        task: Task dict, may contain a 'hooks' key.

    Returns:
        Markdown string for the required-steps section, or empty string.
    """
    agent_hooks = _parse_agent_hooks(task)
    if not agent_hooks:
        return ""

    lines = ["## Required Steps Before Completing Work", ""]
    lines.append("You must complete these steps before finishing:")
    for i, hook in enumerate(agent_hooks, 1):
        name = hook["name"]
        if name == "create_pr":
            # Scheduler handles PR creation — skip this hook for the agent
            continue
        elif name == "run_tests":
            lines.append(f"{i}. Run tests: `../scripts/run-tests`")
        else:
            lines.append(f"{i}. {name}")
    return "\n".join(lines)


def _load_review_section(task_id: str) -> str:
    """Load and format the task message thread for the prompt.

    Args:
        task_id: Task identifier string.

    Returns:
        Formatted markdown review section, or empty string if unavailable.
    """
    if not task_id:
        return ""
    try:
        from .task_thread import get_thread, format_thread_for_prompt
        thread_messages = get_thread(task_id)
        return format_thread_for_prompt(thread_messages)
    except Exception as e:
        logger.debug(f"Failed to load task thread for {task_id}: {e}")
        return ""


def _load_continuation_section(task_id: str, agent_config: dict) -> str:
    """Build the continuation context section for continuer agents.

    Reads the previous agent's stdout tail from prev_stdout.log (written by
    prepare_task_directory before cleaning stdout.log). Returns an empty string
    for non-continuation agents.

    Args:
        task_id: Task identifier
        agent_config: Agent configuration dict (checked for claim_from)

    Returns:
        Markdown continuation section, or empty string for non-continuation agents.
    """
    if agent_config.get("claim_from") != "needs_continuation":
        return ""

    if not task_id:
        return ""

    prev_stdout_path = get_tasks_dir() / task_id / "prev_stdout.log"
    if not prev_stdout_path.exists():
        return ""

    try:
        prev_stdout = prev_stdout_path.read_text(errors="replace").strip()
    except OSError:
        return ""

    if not prev_stdout:
        return ""

    return (
        "## Continuation Context\n\n"
        "You are continuing work on a task that a previous agent started. "
        "Review the existing commits in the worktree and the previous agent's "
        "output below to understand what was done, then continue from where "
        "they left off.\n\n"
        "**Previous agent's output (last 3000 characters):**\n\n"
        f"```\n{prev_stdout}\n```\n"
    )


def _load_intervention_context_for_prompt(task_id: str) -> str:
    """Load intervention context for a task as a formatted JSON string.

    Tries the messages API first (intervention_request message to fixer),
    falling back to intervention_context.json in the task directory.

    Returns empty JSON object string if not found or task_id is empty.
    """
    import json as _json
    if not task_id:
        return "{}"

    # Try messages API first — primary intervention context delivery
    try:
        sdk = queue_utils.get_sdk()
        messages = sdk.messages.list(task_id=task_id, to_actor="fixer", type="intervention_request")
        if messages:
            # Parse the JSON block from the most recent message
            content = messages[-1].get("content", "")
            import re as _re
            match = _re.search(r"```json\s*(.*?)\s*```", content, _re.DOTALL)
            if match:
                return match.group(1)
    except Exception:
        pass

    # Fallback: read from file
    ctx_path = get_tasks_dir() / task_id / "intervention_context.json"
    if ctx_path.exists():
        try:
            return ctx_path.read_text()
        except OSError:
            pass
    return "{}"


def _render_prompt(task: dict, agent_config: dict) -> str:
    """Build the rendered prompt string from template, instructions, hooks, and thread.

    Args:
        task: Task dict with id, title, content, priority, branch, type, hooks.
        agent_config: Agent configuration dict with 'agent_dir' key.

    Returns:
        Fully substituted prompt text (not written to disk here).

    Raises:
        ValueError: If the agent directory or prompt.md is missing.
    """
    from string import Template

    agent_dir = agent_config.get("agent_dir")
    if not agent_dir or not (Path(agent_dir) / "prompt.md").exists():
        raise ValueError(f"Agent directory or prompt.md not found: {agent_dir}")

    prompt_template_path = Path(agent_dir) / "prompt.md"
    prompt_template = prompt_template_path.read_text()
    logger.debug(f"Using prompt template from agent directory: {prompt_template_path}")

    global_instructions = _load_global_instructions(agent_dir)
    required_steps = _build_required_steps(task)
    review_section = _load_review_section(task.get("id", ""))

    # Load intervention context for fixer agents (if available in the task dir)
    intervention_context = _load_intervention_context_for_prompt(task.get("id", ""))

    # Load continuation context for continuer agents (prev_stdout.log written before cleanup)
    continuation_section = _load_continuation_section(task.get("id", ""), agent_config)

    task_dir = get_tasks_dir() / task.get("id", "")

    return Template(prompt_template).safe_substitute(
        task_id=task.get("id", "unknown"),
        task_title=task.get("title", "Untitled"),
        task_content=task.get("content", ""),
        task_priority=task.get("priority", "P2"),
        task_branch=task.get("branch") or get_base_branch(),
        task_type=task.get("type", ""),
        scripts_dir="../scripts",
        global_instructions=global_instructions,
        required_steps=required_steps,
        review_section=review_section,
        continuation_section=continuation_section,
        intervention_context=intervention_context,
        task_dir=str(task_dir),
        worktree=str(task_dir / "worktree"),
    )
