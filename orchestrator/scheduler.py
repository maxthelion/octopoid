#!/usr/bin/env python3
"""Main scheduler - runs on 1-minute ticks to evaluate and spawn agents."""

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from string import Template

from .config import (
    find_parent_project,
    get_agents,
    get_agents_runtime_dir,
    get_commands_dir,
    get_global_instructions_path,
    get_orchestrator_dir,
    get_templates_dir,
)
from .git_utils import ensure_worktree, get_worktree_path
from .lock_utils import locked_or_skip
from .port_utils import get_port_env_vars
from .state_utils import (
    AgentState,
    is_overdue,
    is_process_running,
    load_state,
    mark_finished,
    mark_started,
    save_state,
)


def get_scheduler_lock_path() -> Path:
    """Get path to the global scheduler lock file."""
    return get_orchestrator_dir() / "scheduler.lock"


def get_agent_lock_path(agent_name: str) -> Path:
    """Get path to an agent's lock file."""
    return get_agents_runtime_dir() / agent_name / "lock"


def get_agent_state_path(agent_name: str) -> Path:
    """Get path to an agent's state file."""
    return get_agents_runtime_dir() / agent_name / "state.json"


def get_agent_env_path(agent_name: str) -> Path:
    """Get path to an agent's env.sh file."""
    return get_agents_runtime_dir() / agent_name / "env.sh"


def setup_agent_commands(agent_name: str, role: str) -> None:
    """Copy commands to the agent's worktree.

    Args:
        agent_name: Name of the agent
        role: Agent role (determines which commands to copy)
    """
    worktree_path = get_worktree_path(agent_name)
    commands_dest = worktree_path / ".claude" / "commands"
    commands_dest.mkdir(parents=True, exist_ok=True)

    # Source directories
    submodule_commands = get_commands_dir() / "agent"
    project_overrides = get_orchestrator_dir() / "commands"

    # Copy all agent commands from submodule
    if submodule_commands.exists():
        for cmd_file in submodule_commands.glob("*.md"):
            dest_file = commands_dest / cmd_file.name
            shutil.copy2(cmd_file, dest_file)

    # Override with project-specific commands if they exist
    if project_overrides.exists():
        for cmd_file in project_overrides.glob("*.md"):
            dest_file = commands_dest / cmd_file.name
            shutil.copy2(cmd_file, dest_file)


def generate_agent_instructions(
    agent_name: str,
    role: str,
    agent_config: dict,
    task_info: dict | None = None,
) -> Path:
    """Generate .agent-instructions.md in the agent's worktree.

    Args:
        agent_name: Name of the agent
        role: Agent role
        agent_config: Full agent configuration
        task_info: Optional current task information

    Returns:
        Path to generated instructions file
    """
    worktree_path = get_worktree_path(agent_name)
    instructions_path = worktree_path / ".agent-instructions.md"

    # Load template
    template_path = get_templates_dir() / "agent_instructions.md.tmpl"
    if template_path.exists():
        template_content = template_path.read_text()
    else:
        template_content = DEFAULT_AGENT_INSTRUCTIONS_TEMPLATE

    # Load global instructions
    global_instructions_path = get_global_instructions_path()
    global_instructions = ""
    if global_instructions_path.exists():
        global_instructions = global_instructions_path.read_text()

    # Build task section
    task_section = ""
    if task_info:
        task_section = f"""
## Current Task

**Task ID:** {task_info.get('id', 'unknown')}
**Title:** {task_info.get('title', 'unknown')}
**Priority:** {task_info.get('priority', 'P2')}
**Target Branch:** {task_info.get('branch', 'main')}

{task_info.get('content', '')}
"""

    # Build constraints based on role
    constraints = get_role_constraints(role)

    # Substitute template
    template = Template(template_content)
    content = template.safe_substitute(
        agent_name=agent_name,
        role=role,
        timestamp=datetime.now().isoformat(),
        global_instructions=global_instructions,
        task_section=task_section,
        constraints=constraints,
    )

    instructions_path.write_text(content)
    return instructions_path


def get_role_constraints(role: str) -> str:
    """Get role-specific constraints for agent instructions.

    Args:
        role: Agent role

    Returns:
        Markdown string with constraints
    """
    constraints = {
        "product_manager": """
- You may read any files in the repository
- You may NOT modify code files
- Your output is task files in the queue
- Focus on high-value, well-scoped tasks
- Consider existing PRs and in-progress work
""",
        "implementer": """
- You may read and modify code files
- Create focused, atomic commits
- Follow existing code patterns and conventions
- Write tests for new functionality
- Create a PR when work is complete
""",
        "tester": """
- You may read all files
- You may modify test files only
- Run existing tests and report results
- Add missing test coverage
- Do not modify production code
""",
        "reviewer": """
- You are in READ-ONLY mode by default
- Review code for bugs, security issues, and style
- Leave constructive feedback
- Approve or request changes via PR review
- Do not modify code directly
""",
    }
    return constraints.get(role, "- Follow standard development practices")


def write_agent_env(agent_name: str, agent_id: int, role: str) -> Path:
    """Write environment variables file for an agent.

    Args:
        agent_name: Name of the agent
        agent_id: Numeric ID of the agent
        role: Agent role

    Returns:
        Path to env.sh file
    """
    env_path = get_agent_env_path(agent_name)
    env_path.parent.mkdir(parents=True, exist_ok=True)

    parent_project = find_parent_project()
    worktree_path = get_worktree_path(agent_name)
    shared_dir = get_orchestrator_dir() / "shared"

    port_vars = get_port_env_vars(agent_id)

    lines = [
        "#!/bin/bash",
        f"export AGENT_NAME='{agent_name}'",
        f"export AGENT_ID='{agent_id}'",
        f"export AGENT_ROLE='{role}'",
        f"export PARENT_PROJECT='{parent_project}'",
        f"export WORKTREE='{worktree_path}'",
        f"export SHARED_DIR='{shared_dir}'",
        f"export ORCHESTRATOR_DIR='{get_orchestrator_dir()}'",
    ]

    for key, value in port_vars.items():
        lines.append(f"export {key}='{value}'")

    env_path.write_text("\n".join(lines) + "\n")
    return env_path


def spawn_agent(agent_name: str, agent_id: int, role: str, agent_config: dict) -> int:
    """Spawn an agent subprocess.

    Args:
        agent_name: Name of the agent
        agent_id: Numeric ID of the agent
        role: Agent role
        agent_config: Full agent configuration

    Returns:
        Process ID of spawned agent
    """
    worktree_path = get_worktree_path(agent_name)

    # Build environment
    env = os.environ.copy()
    env["AGENT_NAME"] = agent_name
    env["AGENT_ID"] = str(agent_id)
    env["AGENT_ROLE"] = role
    env["PARENT_PROJECT"] = str(find_parent_project())
    env["WORKTREE"] = str(worktree_path)
    env["SHARED_DIR"] = str(get_orchestrator_dir() / "shared")
    env["ORCHESTRATOR_DIR"] = str(get_orchestrator_dir())

    port_vars = get_port_env_vars(agent_id)
    env.update(port_vars)

    # Determine the role module to run
    role_module = f"orchestrator.orchestrator.roles.{role}"

    # Spawn the role as a subprocess
    process = subprocess.Popen(
        [sys.executable, "-m", role_module],
        cwd=worktree_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # Detach from parent
    )

    return process.pid


def check_and_update_finished_agents() -> None:
    """Check for agents that have finished and update their state."""
    agents_dir = get_agents_runtime_dir()
    if not agents_dir.exists():
        return

    for agent_dir in agents_dir.iterdir():
        if not agent_dir.is_dir():
            continue

        agent_name = agent_dir.name
        state_path = get_agent_state_path(agent_name)
        state = load_state(state_path)

        if state.running and state.pid:
            if not is_process_running(state.pid):
                # Process has finished, update state
                # We don't know the exit code without waiting, assume success
                # In production, you'd use a different mechanism
                new_state = mark_finished(state, 0)
                save_state(new_state, state_path)
                print(f"[{datetime.now().isoformat()}] Agent {agent_name} finished")


def run_scheduler() -> None:
    """Main scheduler loop - evaluate and spawn agents."""
    print(f"[{datetime.now().isoformat()}] Scheduler starting")

    # Check for finished agents first
    check_and_update_finished_agents()

    # Load agent configuration
    try:
        agents = get_agents()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if not agents:
        print("No agents configured in agents.yaml")
        return

    for agent_id, agent_config in enumerate(agents):
        agent_name = agent_config.get("name")
        role = agent_config.get("role")
        interval = agent_config.get("interval_seconds", 300)
        paused = agent_config.get("paused", False)

        if not agent_name or not role:
            print(f"Skipping invalid agent config: {agent_config}")
            continue

        if paused:
            print(f"Agent {agent_name} is paused, skipping")
            continue

        # Try to acquire agent lock
        agent_lock_path = get_agent_lock_path(agent_name)

        with locked_or_skip(agent_lock_path) as acquired:
            if not acquired:
                print(f"Agent {agent_name} is locked (another instance running?)")
                continue

            # Load agent state
            state_path = get_agent_state_path(agent_name)
            state = load_state(state_path)

            # Check if still running
            if state.running and state.pid and is_process_running(state.pid):
                print(f"Agent {agent_name} is still running (PID {state.pid})")
                continue

            # If was marked running but process died, update state
            if state.running:
                state = mark_finished(state, 1)  # Assume crashed
                save_state(state, state_path)

            # Check if overdue
            if not is_overdue(state, interval):
                print(f"Agent {agent_name} is not due yet")
                continue

            print(f"[{datetime.now().isoformat()}] Starting agent {agent_name} (role: {role})")

            # Ensure worktree exists
            base_branch = agent_config.get("base_branch", "main")
            ensure_worktree(agent_name, base_branch)

            # Setup commands in worktree
            setup_agent_commands(agent_name, role)

            # Generate agent instructions
            generate_agent_instructions(agent_name, role, agent_config)

            # Write env file
            write_agent_env(agent_name, agent_id, role)

            # Spawn agent
            pid = spawn_agent(agent_name, agent_id, role, agent_config)

            # Update state
            new_state = mark_started(state, pid)
            save_state(new_state, state_path)

            print(f"Agent {agent_name} started with PID {pid}")

    print(f"[{datetime.now().isoformat()}] Scheduler tick complete")


def main() -> None:
    """Entry point for scheduler."""
    scheduler_lock_path = get_scheduler_lock_path()

    with locked_or_skip(scheduler_lock_path) as acquired:
        if not acquired:
            print("Another scheduler instance is running, exiting")
            sys.exit(0)

        run_scheduler()


# Default template if file doesn't exist
DEFAULT_AGENT_INSTRUCTIONS_TEMPLATE = """# Agent Instructions

**Agent:** $agent_name
**Role:** $role
**Generated:** $timestamp

## Identity

You are an autonomous agent named **$agent_name** with the role of **$role**.
You are part of a multi-agent system coordinated by an orchestrator.

## Global Instructions

$global_instructions

$task_section

## Constraints

$constraints

## Important Notes

- Always commit your changes with clear, descriptive messages
- If you encounter errors, document them clearly
- Do not modify files outside your authorized scope
- Coordinate through the task queue, not direct communication
"""


if __name__ == "__main__":
    main()
