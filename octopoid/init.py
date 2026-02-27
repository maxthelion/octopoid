#!/usr/bin/env python3
"""One-time setup for parent project to use the orchestrator."""

import argparse
import shutil
import sys
from pathlib import Path


def find_parent_project() -> Path:
    """Find the parent project root by walking up to find .git."""
    current = Path(__file__).resolve().parent

    while current != current.parent:
        if current.name == "octopoid" and (current / "octopoid").is_dir():
            current = current.parent
            continue

        if (current / ".git").exists():
            return current
        current = current.parent

    raise RuntimeError(
        "Could not find parent project root. "
        "Make sure orchestrator is installed in a git repository."
    )


def get_package_data_dir() -> Path:
    """Get path to the package data directory bundled with the octopoid package."""
    return Path(__file__).resolve().parent / "data"


def get_orchestrator_submodule() -> Path:
    """Get path to the orchestrator submodule (legacy; prefer get_package_data_dir)."""
    return Path(__file__).resolve().parent.parent


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    """Ask a yes/no question and return the answer."""
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        response = input(prompt + suffix).strip().lower()
        if response == "":
            return default
        if response in ("y", "yes"):
            return True
        if response in ("n", "no"):
            return False
        print("Please answer 'y' or 'n'")


def _register_flows_on_server(flows_dir: Path) -> None:
    """Register all YAML flow files in flows_dir on the server.

    Fails gracefully if the SDK is not configured or the server is unreachable.
    """
    from .flow import Flow, flow_to_server_registration
    from .sdk import get_sdk

    yaml_files = sorted(flows_dir.glob("*.yaml"))
    if not yaml_files:
        return

    try:
        sdk = get_sdk()
    except Exception as e:
        print(f"  Skipping flow registration: {e}")
        print("  Run 'octopoid sync-flows' after configuring server connection")
        return

    for yaml_file in yaml_files:
        try:
            flow = Flow.from_yaml_file(yaml_file)
            reg = flow_to_server_registration(flow)
            sdk.flows.register(
                name=flow.name,
                states=reg["states"],
                transitions=reg["transitions"],
                description=reg.get("description"),
                child_flow=reg.get("child_flow"),
            )
            print(f"  Registered flow '{flow.name}' on server")
        except Exception as e:
            print(f"  Failed to register flow '{yaml_file.stem}': {e}")


def init_orchestrator(
    install_skills: bool | None = None,
    update_gitignore: bool | None = None,
    non_interactive: bool = False,
    mode: str = "local",
) -> None:
    """Initialize orchestrator in the parent project.

    Args:
        install_skills: Install management skills (None = ask)
        update_gitignore: Update .gitignore (None = ask)
        non_interactive: If True, use defaults instead of asking
        mode: Deployment mode - "local" (default) or "remote"
    """
    parent = find_parent_project()
    submodule = get_orchestrator_submodule()
    # Package data directory (bundled with pip-installed package)
    pkg_data = get_package_data_dir()

    print()
    print("  Welcome to Octopoid!")
    print("  An API-driven orchestrator for Claude Code agents.")
    print()
    print(f"  Project: {parent}")
    print(f"  Mode:    {mode}")
    print()

    # Create .octopoid directory structure
    octopoid_dir = parent / ".octopoid"
    runtime_dir = octopoid_dir / "runtime"
    dirs_to_create = [
        octopoid_dir,
        # Runtime (all gitignored)
        runtime_dir / "agents",
        runtime_dir / "tasks",
        runtime_dir / "shared" / "notes",
        runtime_dir / "shared" / "reviews",
        runtime_dir / "logs",
        runtime_dir / "messages",
        # Flow definitions
        octopoid_dir / "flows",
    ]

    created_count = 0
    for d in dirs_to_create:
        if not d.exists():
            created_count += 1
        d.mkdir(parents=True, exist_ok=True)

    # Create project-management directories
    pm_dir = parent / "project-management"
    pm_dirs = [
        pm_dir / "drafts",
        pm_dir / "projects",
        pm_dir / "tasks",
    ]
    for d in pm_dirs:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)

    # Write config files only if they don't exist
    agents_yaml = octopoid_dir / "agents.yaml"
    agents_yaml_created = False
    if not agents_yaml.exists():
        agents_yaml.write_text(EXAMPLE_AGENTS_YAML)
        agents_yaml_created = True

    global_instructions = octopoid_dir / "global-instructions.md"
    gi_created = False
    if not global_instructions.exists():
        global_instructions.write_text(EXAMPLE_GLOBAL_INSTRUCTIONS)
        gi_created = True

    if created_count > 0:
        print(f"  Created .octopoid/ directory structure ({created_count} directories)")
    else:
        print("  Directory structure already exists")
    if agents_yaml_created:
        print("  Created .octopoid/agents.yaml with example configuration")
    else:
        print("  Using existing .octopoid/agents.yaml")
    if gi_created:
        print("  Created .octopoid/global-instructions.md")

    # Create default flow files
    from .flow import generate_default_flow, generate_project_flow

    default_flow_path = octopoid_dir / "flows" / "default.yaml"
    if not default_flow_path.exists():
        default_flow_path.write_text(generate_default_flow())
        print(f"  Created: {default_flow_path.relative_to(parent)}")
    else:
        print(f"  Exists:  {default_flow_path.relative_to(parent)}")

    project_flow_path = octopoid_dir / "flows" / "project.yaml"
    if not project_flow_path.exists():
        project_flow_path.write_text(generate_project_flow())
        print(f"  Created: {project_flow_path.relative_to(parent)}")
    else:
        print(f"  Exists:  {project_flow_path.relative_to(parent)}")

    # Register flow files on the server
    _register_flows_on_server(octopoid_dir / "flows")

    # Scaffold gatekeeper agent directory
    gatekeeper_dir = octopoid_dir / "agents" / "gatekeeper"
    if not gatekeeper_dir.exists():
        # Prefer package data (pip install); fall back to submodule path; then minimal scaffolding
        builtin_gatekeeper = pkg_data / "agents" / "gatekeeper"
        if not builtin_gatekeeper.exists():
            builtin_gatekeeper = submodule / ".octopoid" / "agents" / "gatekeeper"
        if builtin_gatekeeper.exists():
            shutil.copytree(builtin_gatekeeper, gatekeeper_dir)
            print(f"  Created: {gatekeeper_dir.relative_to(parent)}/ (from template)")
        else:
            # Fallback: create minimal scaffolding
            gatekeeper_dir.mkdir(parents=True)
            scripts_dir = gatekeeper_dir / "scripts"
            scripts_dir.mkdir()
            (gatekeeper_dir / "agent.yaml").write_text(GATEKEEPER_AGENT_YAML)
            (gatekeeper_dir / "prompt.md").write_text(GATEKEEPER_PROMPT_STUB)
            (scripts_dir / "run-tests").write_text(GATEKEEPER_RUN_TESTS_SCRIPT)
            (scripts_dir / "run-tests").chmod(0o755)
            print(f"  Created: {gatekeeper_dir.relative_to(parent)}/ (minimal scaffold)")
    else:
        print(f"  Exists:  {gatekeeper_dir.relative_to(parent)}/")

    # Scaffold implementer agent directory
    implementer_dir = octopoid_dir / "agents" / "implementer"
    if not implementer_dir.exists():
        builtin_implementer = pkg_data / "agents" / "implementer"
        if not builtin_implementer.exists():
            builtin_implementer = submodule / ".octopoid" / "agents" / "implementer"
        if builtin_implementer.exists():
            shutil.copytree(builtin_implementer, implementer_dir)
            print(f"  Created: {implementer_dir.relative_to(parent)}/ (from template)")
        else:
            implementer_dir.mkdir(parents=True)
            print(f"  Created: {implementer_dir.relative_to(parent)}/ (empty scaffold)")
    else:
        print(f"  Exists:  {implementer_dir.relative_to(parent)}/")

    # Scaffold codebase-analyst agent directory
    codebase_analyst_dir = octopoid_dir / "agents" / "codebase-analyst"
    if not codebase_analyst_dir.exists():
        builtin_codebase_analyst = pkg_data / "agents" / "codebase-analyst"
        if not builtin_codebase_analyst.exists():
            builtin_codebase_analyst = submodule / ".octopoid" / "agents" / "codebase-analyst"
        if builtin_codebase_analyst.exists():
            shutil.copytree(builtin_codebase_analyst, codebase_analyst_dir)
            print(f"  Created: {codebase_analyst_dir.relative_to(parent)}/ (from template)")
        else:
            codebase_analyst_dir.mkdir(parents=True)
            print(f"  Created: {codebase_analyst_dir.relative_to(parent)}/ (empty scaffold)")
    else:
        print(f"  Exists:  {codebase_analyst_dir.relative_to(parent)}/")

    # Scaffold testing-analyst agent directory
    testing_analyst_dir = octopoid_dir / "agents" / "testing-analyst"
    if not testing_analyst_dir.exists():
        builtin_testing_analyst = pkg_data / "agents" / "testing-analyst"
        if not builtin_testing_analyst.exists():
            builtin_testing_analyst = submodule / ".octopoid" / "agents" / "testing-analyst"
        if builtin_testing_analyst.exists():
            shutil.copytree(builtin_testing_analyst, testing_analyst_dir)
            print(f"  Created: {testing_analyst_dir.relative_to(parent)}/ (from template)")
        else:
            testing_analyst_dir.mkdir(parents=True)
            print(f"  Created: {testing_analyst_dir.relative_to(parent)}/ (empty scaffold)")
    else:
        print(f"  Exists:  {testing_analyst_dir.relative_to(parent)}/")

    # Scaffold architecture-analyst agent directory
    architecture_analyst_dir = octopoid_dir / "agents" / "architecture-analyst"
    if not architecture_analyst_dir.exists():
        builtin_architecture_analyst = pkg_data / "agents" / "architecture-analyst"
        if not builtin_architecture_analyst.exists():
            builtin_architecture_analyst = submodule / ".octopoid" / "agents" / "architecture-analyst"
        if builtin_architecture_analyst.exists():
            shutil.copytree(builtin_architecture_analyst, architecture_analyst_dir)
            print(f"  Created: {architecture_analyst_dir.relative_to(parent)}/ (from template)")
        else:
            architecture_analyst_dir.mkdir(parents=True)
            print(f"  Created: {architecture_analyst_dir.relative_to(parent)}/ (empty scaffold)")
    else:
        print(f"  Exists:  {architecture_analyst_dir.relative_to(parent)}/")

    # Create jobs.yaml only if it does not already exist
    jobs_yaml = octopoid_dir / "jobs.yaml"
    jobs_yaml_created = False
    if not jobs_yaml.exists():
        jobs_yaml.write_text(DEFAULT_JOBS_YAML)
        jobs_yaml_created = True

    if jobs_yaml_created:
        print("  Created .octopoid/jobs.yaml with default scheduler jobs")
    else:
        print("  Using existing .octopoid/jobs.yaml")

    # Install dashboard wrapper script
    dash_script = parent / "octopoid-dash"
    submodule_rel = submodule.relative_to(parent)
    dash_content = DASHBOARD_WRAPPER.replace("__OCTOPOID_DIR__", str(submodule_rel))
    if not dash_script.exists():
        dash_script.write_text(dash_content)
        dash_script.chmod(0o755)
        print(f"  Created: octopoid-dash (dashboard launcher)")
    else:
        # Update if content changed
        if dash_script.read_text() != dash_content:
            dash_script.write_text(dash_content)
            dash_script.chmod(0o755)
            print(f"  Updated: octopoid-dash")
        else:
            print(f"  Exists:  octopoid-dash")

    print()

    # Determine whether to install skills
    if install_skills is None:
        if non_interactive:
            install_skills = True
        else:
            install_skills = ask_yes_no(
                "Install management skills to .claude/commands/? "
                "(enqueue, queue-status, agent-status, etc.)"
            )

    if install_skills:
        claude_commands = parent / ".claude" / "commands"
        claude_commands.mkdir(parents=True, exist_ok=True)

        management_commands = submodule / "commands" / "management"
        if management_commands.exists():
            installed = []
            for cmd_file in sorted(management_commands.glob("*.md")):
                dest = claude_commands / cmd_file.name
                shutil.copy2(cmd_file, dest)
                installed.append(cmd_file.stem)
            if installed:
                print(f"  Installed {len(installed)} management skills to .claude/commands/")
                print(f"    Commands: /{', /'.join(installed)}")
            else:
                print("  No management skills found to install")
    else:
        print("  Skipping skill installation.")
        print("    Run 'octopoid install-commands' later to install them.")

    print()

    # Determine whether to update gitignore
    if update_gitignore is None:
        if non_interactive:
            update_gitignore = True
        else:
            update_gitignore = ask_yes_no("Update .gitignore with octopoid entries?")

    if update_gitignore:
        gitignore = parent / ".gitignore"
        gitignore_additions = GITIGNORE_ADDITIONS.strip().split("\n")

        existing_ignores = set()
        if gitignore.exists():
            existing_ignores = set(gitignore.read_text().strip().split("\n"))

        new_ignores = [line for line in gitignore_additions if line not in existing_ignores]
        if new_ignores:
            with open(gitignore, "a") as f:
                f.write("\n# Octopoid\n")
                for line in new_ignores:
                    f.write(f"{line}\n")
            print(f"  Updated .gitignore ({len(new_ignores)} entries added)")
        else:
            print("  .gitignore already has octopoid entries")
    else:
        print("  Skipping .gitignore update.")
        print("    Run again with --gitignore to add entries, or add these manually:")
        for line in GITIGNORE_ADDITIONS.strip().split("\n"):
            if line and not line.startswith("#"):
                print(f"      {line}")

    # Print success and next steps
    print()
    print("=" * 60)
    print("  Setup complete!")
    print("=" * 60)
    print()
    print("  Next steps:")
    print()
    print("  1. Update your project's CLAUDE.md (create one if needed):")
    print()
    print("     Add these two lines so agents can find their instructions:")
    print()
    print("       If .agent-instructions.md exists in this directory,")
    print("       read and follow those instructions.")
    print()
    print("  2. Configure your agents in .octopoid/agents.yaml")
    print("     The default config includes one implementer agent.")
    print("     Adjust agent names, roles, models, and intervals as needed.")
    print()
    print("  3. Start the scheduler:")
    print()
    print("     # Run once (good for testing):")
    print(f"     python {submodule.relative_to(parent)}/orchestrator/scheduler.py")
    print()
    print("  4. Create your first task:")
    print()
    print("     /enqueue")
    print()
    print("  5. Check status:")
    print()
    print("     /queue-status     # see task queue")
    print("     /agent-status     # see agent states")
    print("     ./octopoid-dash   # launch TUI dashboard")
    print()
    print("  Documentation: orchestrator/README.md")
    print()


EXAMPLE_AGENTS_YAML = """# Octopoid Agent Configuration
# See orchestrator/README.md for documentation

# Queue limits for backpressure control
queue_limits:
  max_incoming: 20   # Max tasks in incoming + claimed
  max_claimed: 1     # Max tasks being worked on simultaneously
  max_provisional: 10   # Max tasks awaiting review

# Agent blueprints — each key is a blueprint name
agents:
  implementer:
    role: implement
    spawn_mode: scripts
    agent_dir: .octopoid/agents/implementer
    max_instances: 1   # How many instances can run concurrently
    interval_seconds: 180  # 3 minutes

  sanity-check-gatekeeper:
    role: gatekeeper
    spawn_mode: scripts
    claim_from: provisional
    interval_seconds: 120
    max_turns: 100
    model: sonnet
    agent_dir: .octopoid/agents/gatekeeper
    max_instances: 1
"""

EXAMPLE_GLOBAL_INSTRUCTIONS = """# Global Agent Instructions

These instructions apply to all agents in the orchestrator.

## Code Standards
- Follow existing code patterns and conventions
- Write tests for new functionality
- Create focused, atomic commits
"""

GATEKEEPER_AGENT_YAML = """role: gatekeeper
model: sonnet
max_turns: 100
interval_seconds: 120
spawn_mode: scripts
lightweight: false
allowed_tools:
  - Read
  - Glob
  - Grep
  - Bash
"""

GATEKEEPER_PROMPT_STUB = """# Gatekeeper Review

Review the task's implementation against its acceptance criteria.
Run the scripts in `../scripts/` for automated checks, then review the diff.

Write your decision clearly to stdout:
- To approve: end your output with "APPROVED" or "DECISION: APPROVED"
- To reject: end your output with "REJECTED" or "DECISION: REJECTED" and explain why

When rejecting, you MUST also rewrite the task file with concrete code examples
showing the correct implementation. The implementing agent reads only the task file,
not PR comments. Show the target code — never name forbidden patterns.
"""

GATEKEEPER_RUN_TESTS_SCRIPT = """#!/bin/bash
# Run the project test suite. Customize this for your project.
# Exit 0 if tests pass, 1 if they fail.
echo "No test runner configured. Edit .octopoid/agents/gatekeeper/scripts/run-tests"
exit 0
"""

GITIGNORE_ADDITIONS = """
# Octopoid runtime files
.octopoid/runtime/
.octopoid/tasks/
.agent-instructions.md
"""

DEFAULT_JOBS_YAML = """# Declarative scheduler job definitions.
#
# Each job runs on a fixed interval (seconds). The launchd tick is 10s;
# jobs only run when their interval has elapsed since last run.
#
# Fields:
#   name     — job name, must match a @register_job function in jobs.py
#   interval — minimum seconds between runs
#   type     — "script" (Python function) or "agent" (spawns a Claude agent)
#   group    — "local" (no API calls, runs before poll fetch)
#              "remote" (runs after poll fetch, ctx.poll_data is populated)
#
# For type: agent jobs, additional fields are supported:
#   blueprint    — pool blueprint name (defaults to job name)
#   max_instances — max concurrent agent instances (default: 1)
#   agent_config  — dict merged into the AgentContext agent_config

jobs:

  # --------------------------------------------------------------------------
  # Local jobs — run before the poll fetch, no API calls needed
  # --------------------------------------------------------------------------

  # Fast PID check: detect finished agents and process their results.
  # Runs every 10s (local PID checks only — no network I/O).
  - name: check_and_update_finished_agents
    interval: 10
    type: script
    group: local

  # --------------------------------------------------------------------------
  # Remote jobs — run after the poll fetch, ctx.poll_data is populated
  # --------------------------------------------------------------------------

  # Register this orchestrator with the server (idempotent).
  # Uses poll_data.orchestrator_registered to skip the POST when not needed.
  - name: _register_orchestrator
    interval: 300
    type: script
    group: remote

  # Send a heartbeat to the server so queue-status shows a recent "Last tick".
  - name: send_heartbeat
    interval: 60
    type: script
    group: remote

  # Requeue tasks whose claim lease has expired (server-side fallback).
  - name: check_and_requeue_expired_leases
    interval: 60
    type: script
    group: remote

  # Detect projects whose children are all done and run flow transitions.
  - name: check_project_completion
    interval: 60
    type: script
    group: remote

  # Check queue health (already self-throttled internally at 30 min).
  - name: _check_queue_health_throttled
    interval: 1800
    type: script
    group: remote

  # Main agent evaluation and spawning loop.
  # Uses poll_data.queue_counts for backpressure without per-agent API calls.
  - name: agent_evaluation_loop
    interval: 60
    type: script
    group: remote

  # Archive logs and delete worktrees/branches for old done/failed tasks.
  - name: sweep_stale_resources
    interval: 1800
    type: script
    group: remote

  # Poll GitHub issues and create tasks for new ones.
  # Rate budget: 1 gh issue list call per run = 4 calls/hour (< 0.1% of 5000/hour limit).
  # Issues labelled 'server' are forwarded to the server repo instead.
  - name: poll_github_issues
    interval: 900
    type: script
    group: local

  # Message dispatcher: poll action_command messages and spawn action agents.
  # Processes one message per tick (serial). Runs every 30s so unprocessed
  # messages are handled promptly. Agent runs synchronously (max 3 minutes).
  - name: dispatch_action_messages
    interval: 30
    type: script
    group: remote

  # Daily codebase analyst: scans for large/complex files and proposes refactoring.
  # Guard script runs first inside the agent — skips if a pending proposal already exists.
  - name: codebase_analyst
    interval: 86400
    type: agent
    group: remote
    max_instances: 1
    agent_config:
      role: analyse
      spawn_mode: scripts
      lightweight: true
      agent_dir: .octopoid/agents/codebase-analyst

  # Daily testing analyst: scans for test coverage gaps and proposes specific tests.
  # Focuses on outside-in gaps — features that shipped with no tests, or only over-mocked
  # unit tests with no integration coverage. Guard skips if a proposal already exists.
  - name: testing_analyst
    interval: 86400
    type: agent
    group: remote
    max_instances: 1
    agent_config:
      role: analyse
      spawn_mode: scripts
      lightweight: true
      agent_dir: .octopoid/agents/testing-analyst

  # Daily architecture analyst: scans for complex functions, copy-paste,
  # and structural issues using Lizard and jscpd. Proposes refactorings
  # with named design patterns. Guard skips if a proposal already exists.
  - name: architecture_analyst
    interval: 86400
    type: agent
    group: remote
    max_instances: 1
    agent_config:
      role: analyse
      spawn_mode: scripts
      lightweight: true
      agent_dir: .octopoid/agents/architecture-analyst
"""

DASHBOARD_WRAPPER = """#!/usr/bin/env bash
# Octopoid Dashboard — Textual TUI for monitoring the orchestrator.
# Generated by 'octopoid init'. Safe to regenerate with 'octopoid init'.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OCTOPOID_DIR="$SCRIPT_DIR/__OCTOPOID_DIR__"
export PYTHONPATH="$OCTOPOID_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m packages.dashboard "$@"
""".lstrip()


def main():
    parser = argparse.ArgumentParser(
        description="Initialize orchestrator in the parent project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python init.py                       # Interactive
  python init.py -y                    # Accept all defaults
  python init.py --no-skills           # Skip skill installation
  python init.py --skills --gitignore  # Install skills and update gitignore
""",
    )

    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Non-interactive mode, accept all defaults",
    )
    parser.add_argument(
        "--skills",
        action="store_true",
        default=None,
        help="Install management skills to .claude/commands/",
    )
    parser.add_argument(
        "--no-skills",
        action="store_true",
        help="Do not install management skills",
    )
    parser.add_argument(
        "--gitignore",
        action="store_true",
        default=None,
        help="Update .gitignore with octopoid entries",
    )
    parser.add_argument(
        "--no-gitignore",
        action="store_true",
        help="Do not update .gitignore",
    )

    args = parser.parse_args()

    # Resolve skill flags
    install_skills = None
    if args.skills:
        install_skills = True
    elif args.no_skills:
        install_skills = False

    # Resolve gitignore flags
    update_gitignore = None
    if args.gitignore:
        update_gitignore = True
    elif args.no_gitignore:
        update_gitignore = False

    init_orchestrator(
        install_skills=install_skills,
        update_gitignore=update_gitignore,
        non_interactive=args.yes,
    )


if __name__ == "__main__":
    main()
