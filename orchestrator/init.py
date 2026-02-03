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
) -> None:
    """Initialize orchestrator in the parent project.

    Args:
        install_skills: Install management skills (None = ask)
        update_gitignore: Update .gitignore (None = ask)
        non_interactive: If True, use defaults instead of asking
    """
    parent = find_parent_project()
    submodule = get_orchestrator_submodule()

    print(f"Initializing orchestrator in: {parent}")
    print()

    # Create .orchestrator directory structure
    orchestrator_dir = parent / ".orchestrator"
    dirs_to_create = [
        orchestrator_dir,
        orchestrator_dir / "commands",
        orchestrator_dir / "agents",
        orchestrator_dir / "messages",
        orchestrator_dir / "prompts",  # For proposer prompts
        # Task queue
        orchestrator_dir / "shared" / "queue" / "incoming",
        orchestrator_dir / "shared" / "queue" / "claimed",
        orchestrator_dir / "shared" / "queue" / "done",
        orchestrator_dir / "shared" / "queue" / "failed",
        # Proposal queue (for proposal model)
        orchestrator_dir / "shared" / "proposals" / "active",
        orchestrator_dir / "shared" / "proposals" / "promoted",
        orchestrator_dir / "shared" / "proposals" / "deferred",
        orchestrator_dir / "shared" / "proposals" / "rejected",
    ]

    print("Creating directory structure...")
    for d in dirs_to_create:
        d.mkdir(parents=True, exist_ok=True)
        print(f"  {d.relative_to(parent)}/")

    # Copy example agents.yaml if not exists
    agents_yaml = orchestrator_dir / "agents.yaml"
    if not agents_yaml.exists():
        agents_yaml.write_text(EXAMPLE_AGENTS_YAML)
        print(f"  Created: {agents_yaml.relative_to(parent)}")
    else:
        print(f"  Exists:  {agents_yaml.relative_to(parent)}")

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
            print("Installing management skills...")
            for cmd_file in management_commands.glob("*.md"):
                dest = claude_commands / cmd_file.name
                shutil.copy2(cmd_file, dest)
                print(f"  .claude/commands/{cmd_file.name}")
    else:
        print("Skipping skill installation.")
        print("  You can manually copy from orchestrator/commands/management/ later.")

    print()

    # Determine whether to update gitignore
    if update_gitignore is None:
        if non_interactive:
            update_gitignore = True
        else:
            update_gitignore = ask_yes_no("Update .gitignore with orchestrator entries?")

    if update_gitignore:
        gitignore = parent / ".gitignore"
        gitignore_additions = GITIGNORE_ADDITIONS.strip().split("\n")

        existing_ignores = set()
        if gitignore.exists():
            existing_ignores = set(gitignore.read_text().strip().split("\n"))

        new_ignores = [line for line in gitignore_additions if line not in existing_ignores]
        if new_ignores:
            with open(gitignore, "a") as f:
                f.write("\n# Orchestrator\n")
                for line in new_ignores:
                    f.write(f"{line}\n")
            print("Updated .gitignore")
        else:
            print("Gitignore entries already present")
    else:
        print("Skipping .gitignore update.")
        print("  Recommended entries:")
        for line in GITIGNORE_ADDITIONS.strip().split("\n"):
            if line:
                print(f"    {line}")

    # Print instructions
    print()
    print("=" * 60)
    print("Setup complete!")
    print("=" * 60)
    print()
    print("Next steps:")
    print()
    print("1. Add these lines to your claude.md (or create one):")
    print()
    print("   ---")
    print("   If .agent-instructions.md exists in this directory,")
    print("   read and follow those instructions.")
    print()
    print("   Check .orchestrator/messages/ for any agent messages")
    print("   and inform the user of warnings or questions.")
    print("   ---")
    print()
    print("2. Configure agents in .orchestrator/agents.yaml")
    print()
    print("3. Set up the scheduler to run every minute:")
    print()
    print("   # crontab -e")
    print(f"   * * * * * cd {parent} && python orchestrator/orchestrator/scheduler.py >> /var/log/orchestrator.log 2>&1")
    print()
    print("4. Create your first task:")
    print()
    print("   /enqueue")
    print()


EXAMPLE_AGENTS_YAML = """# Orchestrator Agent Configuration
# See orchestrator/README.md for documentation

# Model: "task" (v1) or "proposal" (v2)
# - task: PM creates tasks directly
# - proposal: Proposers create proposals, curator promotes to tasks
model: task

# Queue limits for backpressure control
queue_limits:
  max_incoming: 20   # Max tasks in incoming + claimed
  max_claimed: 5     # Max tasks being worked on simultaneously
  max_open_prs: 10   # Max open pull requests

# Proposal limits (for proposal model)
# proposal_limits:
#   test-checker:
#     max_active: 5      # Max proposals in queue
#     max_per_run: 2     # Max proposals per invocation
#   architect:
#     max_active: 3
#     max_per_run: 1

# Voice weights - proposer trust levels (for proposal model)
# voice_weights:
#   plan-reader: 1.5    # Executing plans is priority
#   architect: 1.2      # Simplification multiplies velocity
#   test-checker: 1.0   # Important but often not urgent
#   app-designer: 0.8   # Features after stability

# Curator scoring weights (for proposal model)
# curator_scoring:
#   priority_alignment: 0.30
#   complexity_reduction: 0.25
#   risk: 0.15
#   dependencies_met: 0.15
#   voice_weight: 0.15

# Agent definitions
agents:
  # --- Task Model (v1) ---
  - name: pm-agent
    role: product_manager
    interval_seconds: 600  # 10 minutes

  - name: impl-agent-1
    role: implementer
    interval_seconds: 180  # 3 minutes

  # --- Proposal Model (v2) - uncomment to use ---
  # Proposers - specialized agents that propose work
  # - name: test-checker
  #   role: proposer
  #   focus: test_quality
  #   interval_seconds: 86400  # Daily

  # - name: architect
  #   role: proposer
  #   focus: code_structure
  #   interval_seconds: 86400  # Daily

  # Curator - evaluates proposals
  # - name: curator
  #   role: curator
  #   interval_seconds: 600  # Every 10 min

  # --- Execution layer (both models) ---
  # - name: impl-agent-2
  #   role: implementer
  #   interval_seconds: 180

  # - name: test-agent
  #   role: tester
  #   interval_seconds: 120

  # - name: review-agent
  #   role: reviewer
  #   interval_seconds: 300
"""

GITIGNORE_ADDITIONS = """
# Orchestrator runtime files
.orchestrator/agents/
.orchestrator/messages/
.orchestrator/shared/queue/claimed/
.orchestrator/shared/queue/failed/
.orchestrator/shared/proposals/
.orchestrator/scheduler.lock
.agent-instructions.md
"""


def main():
    parser = argparse.ArgumentParser(
        description="Initialize orchestrator in the parent project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python init.py                     # Interactive mode
  python init.py -y                  # Accept all defaults
  python init.py --no-skills         # Skip skill installation
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
        help="Update .gitignore with orchestrator entries",
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
