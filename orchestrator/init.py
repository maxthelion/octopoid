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
        if current.name == "orchestrator" and (current / "orchestrator").is_dir():
            current = current.parent
            continue

        if (current / ".git").exists():
            return current
        current = current.parent

    raise RuntimeError(
        "Could not find parent project root. "
        "Make sure orchestrator is installed in a git repository."
    )


def get_orchestrator_submodule() -> Path:
    """Get path to the orchestrator submodule."""
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
        # Task content (gitignored, server is source of truth)
        octopoid_dir / "tasks",
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
        print("    Run again with --skills to install, or copy from")
        print("    orchestrator/commands/management/ manually.")

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
    print()
    print("  Documentation: orchestrator/README.md")
    print()


EXAMPLE_AGENTS_YAML = """# Octopoid Agent Configuration
# See orchestrator/README.md for documentation

# Queue limits for backpressure control
queue_limits:
  max_incoming: 20   # Max tasks in incoming + claimed
  max_claimed: 1     # Max tasks being worked on simultaneously
  max_open_prs: 10   # Max open pull requests

# Agent definitions
agents:
  - name: implementer-1
    role: implementer
    interval_seconds: 180  # 3 minutes

  # - name: implementer-2
  #   role: implementer
  #   interval_seconds: 180

  # - name: github-issue-monitor
  #   role: github_issue_monitor
  #   interval_seconds: 300
  #   lightweight: true
"""

EXAMPLE_GLOBAL_INSTRUCTIONS = """# Global Agent Instructions

These instructions apply to all agents in the orchestrator.

## Code Standards
- Follow existing code patterns and conventions
- Write tests for new functionality
- Create focused, atomic commits
"""

GITIGNORE_ADDITIONS = """
# Octopoid runtime files
.octopoid/runtime/
.octopoid/tasks/
.agent-instructions.md
"""


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
