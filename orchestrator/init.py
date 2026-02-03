#!/usr/bin/env python3
"""One-time setup for parent project to use the orchestrator."""

import shutil
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


def init_orchestrator() -> None:
    """Initialize orchestrator in the parent project."""
    parent = find_parent_project()
    submodule = get_orchestrator_submodule()

    print(f"Initializing orchestrator in: {parent}")

    # Create .orchestrator directory structure
    orchestrator_dir = parent / ".orchestrator"
    dirs_to_create = [
        orchestrator_dir,
        orchestrator_dir / "commands",
        orchestrator_dir / "agents",
        orchestrator_dir / "shared" / "queue" / "incoming",
        orchestrator_dir / "shared" / "queue" / "claimed",
        orchestrator_dir / "shared" / "queue" / "done",
        orchestrator_dir / "shared" / "queue" / "failed",
    ]

    for d in dirs_to_create:
        d.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {d.relative_to(parent)}")

    # Copy example agents.yaml if not exists
    agents_yaml = orchestrator_dir / "agents.yaml"
    if not agents_yaml.exists():
        agents_yaml.write_text(EXAMPLE_AGENTS_YAML)
        print(f"  Created: {agents_yaml.relative_to(parent)}")
    else:
        print(f"  Exists:  {agents_yaml.relative_to(parent)}")

    # Copy global-instructions.md if not exists
    global_instructions = orchestrator_dir / "global-instructions.md"
    if not global_instructions.exists():
        global_instructions.write_text(EXAMPLE_GLOBAL_INSTRUCTIONS)
        print(f"  Created: {global_instructions.relative_to(parent)}")
    else:
        print(f"  Exists:  {global_instructions.relative_to(parent)}")

    # Copy management commands to .claude/commands/
    claude_commands = parent / ".claude" / "commands"
    claude_commands.mkdir(parents=True, exist_ok=True)

    management_commands = submodule / "commands" / "management"
    if management_commands.exists():
        for cmd_file in management_commands.glob("*.md"):
            dest = claude_commands / cmd_file.name
            shutil.copy2(cmd_file, dest)
            print(f"  Copied:  .claude/commands/{cmd_file.name}")

    # Update .gitignore
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
        print(f"  Updated: .gitignore")
    else:
        print(f"  Exists:  .gitignore entries")

    # Print instructions
    print()
    print("=" * 60)
    print("Setup complete!")
    print("=" * 60)
    print()
    print("Next steps:")
    print()
    print("1. Add this line to your claude.md (or create one):")
    print()
    print('   If .agent-instructions.md exists in this directory,')
    print('   read and follow those instructions.')
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

# Queue limits for backpressure control
queue_limits:
  max_incoming: 20   # Max tasks in incoming + claimed
  max_claimed: 5     # Max tasks being worked on simultaneously
  max_open_prs: 10   # Max open pull requests

# Agent definitions
agents:
  - name: pm-agent
    role: product_manager
    interval_seconds: 600  # 10 minutes

  - name: impl-agent-1
    role: implementer
    interval_seconds: 180  # 3 minutes

  # Uncomment to add more agents:
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

EXAMPLE_GLOBAL_INSTRUCTIONS = """# Global Agent Instructions

These instructions apply to all orchestrator agents working on this project.

## Project Context

[Describe your project here - what it does, key technologies, etc.]

## Code Standards

- Follow existing patterns in the codebase
- Write clear, self-documenting code
- Add tests for new functionality

## Git Workflow

- Create atomic commits with clear messages
- Base feature branches on `main` unless specified otherwise
- Keep pull requests focused and reviewable

## Important Notes

[Add project-specific guidelines here]
"""

GITIGNORE_ADDITIONS = """
# Orchestrator runtime files
.orchestrator/agents/
.orchestrator/shared/queue/claimed/
.orchestrator/shared/queue/failed/
.orchestrator/scheduler.lock
.agent-instructions.md
"""


if __name__ == "__main__":
    init_orchestrator()
