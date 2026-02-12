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
    print("  A file-driven orchestrator for Claude Code agents.")
    print()
    print(f"  Project: {parent}")
    print(f"  Mode:    {mode}")
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
        orchestrator_dir / "shared" / "queue" / "rejected",
        # Proposal queue (for proposal model)
        orchestrator_dir / "shared" / "proposals" / "active",
        orchestrator_dir / "shared" / "proposals" / "promoted",
        orchestrator_dir / "shared" / "proposals" / "deferred",
        orchestrator_dir / "shared" / "proposals" / "rejected",
    ]

    created_count = 0
    for d in dirs_to_create:
        if not d.exists():
            created_count += 1
        d.mkdir(parents=True, exist_ok=True)

    # Copy example agents.yaml if not exists
    agents_yaml = orchestrator_dir / "agents.yaml"
    agents_yaml_created = False
    if not agents_yaml.exists():
        agents_yaml.write_text(EXAMPLE_AGENTS_YAML)
        agents_yaml_created = True

    if created_count > 0:
        print(f"  Created .orchestrator/ directory structure ({created_count} directories)")
    else:
        print("  Directory structure already exists")
    if agents_yaml_created:
        print("  Created .orchestrator/agents.yaml with example configuration")
    else:
        print("  Using existing .orchestrator/agents.yaml")

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
            print(f"  Updated .gitignore ({len(new_ignores)} entries added)")
        else:
            print("  .gitignore already has orchestrator entries")
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
    print("       Check .orchestrator/messages/ for any agent messages")
    print("       and inform the user of warnings or questions.")
    print()
    print("  2. Configure your agents in .orchestrator/agents.yaml")
    print("     The default config includes a PM agent and one implementer.")
    print("     Adjust agent names, roles, models, and intervals as needed.")
    print()
    print("  3. Start the scheduler:")
    print()
    print("     # Run once (good for testing):")
    print(f"     python {submodule.relative_to(parent)}/orchestrator/scheduler.py")
    print()
    print("     # Run on a schedule (crontab -e):")
    print(f"     * * * * * cd {parent} && python {submodule.relative_to(parent)}/orchestrator/scheduler.py >> /var/log/orchestrator.log 2>&1")
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

# SQLite database backend (optional, replaces file-based queues)
# database:
#   enabled: false     # Set to true to use SQLite
#   path: state.db     # Path relative to .orchestrator/

# Pre-check settings (scheduler-level submission filtering, requires database enabled)
# pre_check:
#   require_commits: true           # Reject tasks with no commits
#   max_attempts_before_planning: 3 # Escalate to planning after N failures
#   claim_timeout_minutes: 60       # Reset stuck claimed tasks after N minutes

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

  # --- Pre-check layer (scheduler-level submission filtering) ---
  # - name: pre-checker
  #   role: pre_check
  #   interval_seconds: 60    # Check provisional queue frequently
  #   lightweight: true       # No worktree needed
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
Deployment modes:
  Local mode (default):
    SQLite database, scheduler runs on this machine.
    Best for single-user, local development.

  Remote mode (not yet available):
    Cloudflare Workers backend for distributed teams.

Examples:
  python init.py                       # Local mode, interactive
  python init.py --local               # Local mode (explicit)
  python init.py -y                    # Accept all defaults
  python init.py --no-skills           # Skip skill installation
  python init.py --skills --gitignore  # Install skills and update gitignore
""",
    )

    # Mode selection
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--local",
        action="store_true",
        default=False,
        help="Use local mode with SQLite database (default)",
    )
    mode_group.add_argument(
        "--server",
        metavar="URL",
        default=None,
        help="Use remote mode with a Cloudflare Workers backend (not yet available)",
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

    # Resolve mode
    if args.server is not None:
        print()
        print("  Remote mode is not yet available.")
        print()
        print("  Octopoid currently supports local mode only:")
        print("    python init.py          # default (local mode)")
        print("    python init.py --local  # explicit local mode")
        print()
        print("  Remote mode with Cloudflare Workers is planned for a future release.")
        print()
        sys.exit(1)

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
