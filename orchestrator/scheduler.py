#!/usr/bin/env python3
"""Main scheduler - runs on 1-minute ticks to evaluate and spawn agents."""

import argparse
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

# Global debug flag
DEBUG = False
_log_file: Path | None = None


def setup_scheduler_debug() -> None:
    """Set up debug logging for the scheduler."""
    global _log_file
    logs_dir = get_orchestrator_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    _log_file = logs_dir / f"scheduler-{date_str}.log"


def debug_log(message: str) -> None:
    """Write a debug message to the scheduler log."""
    if not DEBUG or not _log_file:
        return

    timestamp = datetime.now().isoformat()
    log_line = f"[{timestamp}] [SCHEDULER] {message}\n"

    try:
        with open(_log_file, "a") as f:
            f.write(log_line)
    except OSError:
        pass


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
        # Task model (v1)
        "product_manager": """
- You may read any files in the repository
- You may NOT modify code files
- Your output is task files in the queue
- Focus on high-value, well-scoped tasks
- Consider existing PRs and in-progress work
""",
        # Proposal model (v2)
        "proposer": """
- You may read any files in the repository
- You may NOT modify code files
- Your output is proposal files
- Stay focused on your designated area
- Review your rejected proposals before creating new ones
- Create well-scoped, actionable proposals
""",
        "curator": """
- You may read any files in the repository
- You may NOT modify code files
- Evaluate proposals based on project priorities
- Provide constructive feedback when rejecting
- Escalate conflicts to the project owner
- Do not explore the codebase directly
""",
        # Gatekeeper system
        "gatekeeper": """
- You may read any files in the repository
- You may NOT modify code files
- Review the PR diff from your specialized perspective
- Be thorough but fair in your assessment
- Provide specific, actionable feedback for any issues
- Record your check result using the /record-check skill
""",
        "gatekeeper_coordinator": """
- You may read any files in the repository
- You may NOT modify code files
- Monitor PRs and coordinate gatekeeper checks
- Aggregate check results
- Create fix tasks when checks fail
- Approve PRs when all checks pass
""",
        # Execution layer (both models)
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


def write_agent_env(agent_name: str, agent_id: int, role: str, agent_config: dict | None = None) -> Path:
    """Write environment variables file for an agent.

    Args:
        agent_name: Name of the agent
        agent_id: Numeric ID of the agent
        role: Agent role
        agent_config: Optional agent configuration for extra vars

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

    # Add focus for proposers and gatekeepers (specialists)
    if agent_config and role in ("proposer", "gatekeeper") and "focus" in agent_config:
        lines.append(f"export AGENT_FOCUS='{agent_config['focus']}'")

    # Pass debug mode
    if DEBUG:
        lines.append("export ORCHESTRATOR_DEBUG='1'")

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

    # Pass focus for proposers and gatekeepers (specialists)
    if role in ("proposer", "gatekeeper") and "focus" in agent_config:
        env["AGENT_FOCUS"] = agent_config["focus"]

    # Pass debug mode to agents
    if DEBUG:
        env["ORCHESTRATOR_DEBUG"] = "1"

    port_vars = get_port_env_vars(agent_id)
    env.update(port_vars)

    # Determine the role module to run
    role_module = f"orchestrator.orchestrator.roles.{role}"

    debug_log(f"Spawning agent {agent_name}: module={role_module}, cwd={worktree_path}")
    debug_log(f"Agent env: AGENT_FOCUS={env.get('AGENT_FOCUS', 'N/A')}, ports={port_vars}")

    # Spawn the role as a subprocess
    process = subprocess.Popen(
        [sys.executable, "-m", role_module],
        cwd=worktree_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # Detach from parent
    )

    debug_log(f"Agent {agent_name} spawned with PID {process.pid}")
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
    debug_log("Scheduler tick starting")

    # Check for finished agents first
    check_and_update_finished_agents()

    # Load agent configuration
    try:
        agents = get_agents()
        debug_log(f"Loaded {len(agents)} agents from config")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        debug_log(f"Failed to load agents config: {e}")
        sys.exit(1)

    if not agents:
        print("No agents configured in agents.yaml")
        debug_log("No agents configured")
        return

    for agent_id, agent_config in enumerate(agents):
        agent_name = agent_config.get("name")
        role = agent_config.get("role")
        interval = agent_config.get("interval_seconds", 300)
        paused = agent_config.get("paused", False)

        if not agent_name or not role:
            print(f"Skipping invalid agent config: {agent_config}")
            debug_log(f"Invalid agent config: {agent_config}")
            continue

        if paused:
            print(f"Agent {agent_name} is paused, skipping")
            debug_log(f"Agent {agent_name} is paused")
            continue

        debug_log(f"Evaluating agent {agent_name}: role={role}, interval={interval}s")

        # Try to acquire agent lock
        agent_lock_path = get_agent_lock_path(agent_name)

        with locked_or_skip(agent_lock_path) as acquired:
            if not acquired:
                print(f"Agent {agent_name} is locked (another instance running?)")
                debug_log(f"Agent {agent_name} lock not acquired")
                continue

            # Load agent state
            state_path = get_agent_state_path(agent_name)
            state = load_state(state_path)
            debug_log(f"Agent {agent_name} state: running={state.running}, pid={state.pid}, last_finished={state.last_finished}")

            # Check if still running
            if state.running and state.pid and is_process_running(state.pid):
                print(f"Agent {agent_name} is still running (PID {state.pid})")
                debug_log(f"Agent {agent_name} still running (PID {state.pid})")
                continue

            # If was marked running but process died, update state
            if state.running:
                debug_log(f"Agent {agent_name} was marked running but process died, marking as crashed")
                state = mark_finished(state, 1)  # Assume crashed
                save_state(state, state_path)

            # Check if overdue
            if not is_overdue(state, interval):
                print(f"Agent {agent_name} is not due yet")
                debug_log(f"Agent {agent_name} not due yet")
                continue

            print(f"[{datetime.now().isoformat()}] Starting agent {agent_name} (role: {role})")
            debug_log(f"Starting agent {agent_name} (role: {role})")

            # Ensure worktree exists
            base_branch = agent_config.get("base_branch", "main")
            debug_log(f"Ensuring worktree for {agent_name} on branch {base_branch}")
            ensure_worktree(agent_name, base_branch)

            # Setup commands in worktree
            debug_log(f"Setting up commands for {agent_name}")
            setup_agent_commands(agent_name, role)

            # Generate agent instructions
            debug_log(f"Generating instructions for {agent_name}")
            generate_agent_instructions(agent_name, role, agent_config)

            # Write env file
            debug_log(f"Writing env file for {agent_name}")
            write_agent_env(agent_name, agent_id, role, agent_config)

            # Spawn agent
            pid = spawn_agent(agent_name, agent_id, role, agent_config)

            # Update state
            new_state = mark_started(state, pid)
            save_state(new_state, state_path)

            print(f"Agent {agent_name} started with PID {pid}")

    print(f"[{datetime.now().isoformat()}] Scheduler tick complete")
    debug_log("Scheduler tick complete")


def main() -> None:
    """Entry point for scheduler."""
    global DEBUG

    parser = argparse.ArgumentParser(description="Run the orchestrator scheduler")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging to .orchestrator/logs/",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (don't wait for lock)",
    )
    args = parser.parse_args()

    DEBUG = args.debug
    if DEBUG:
        setup_scheduler_debug()
        debug_log("Scheduler starting with debug mode enabled")
        print("Debug mode enabled - logs in .orchestrator/logs/")

    scheduler_lock_path = get_scheduler_lock_path()

    with locked_or_skip(scheduler_lock_path) as acquired:
        if not acquired:
            print("Another scheduler instance is running, exiting")
            debug_log("Scheduler lock not acquired - another instance running")
            sys.exit(0)

        debug_log("Scheduler lock acquired")
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
