"""Command whitelist and permission export for IDE integration.

Generates IDE-specific permission configurations so users can bulk-approve
the commands Octopoid agents need, instead of being prompted per-command.

Supports:
  - claude-code: Generates allowedTools entries for .claude/settings.json
"""

import argparse
import json
import sys
from typing import Any

from .config import get_commands_config, get_file_patterns


def build_bash_patterns(commands: dict[str, list[str]]) -> list[str]:
    """Build Bash tool permission patterns from command config.

    Each pattern matches a command group and its subcommands using regex
    alternation. For example:
        git: [status, fetch, checkout]
        becomes: "git (status|fetch|checkout)"

    Args:
        commands: Dictionary mapping command group to subcommand list

    Returns:
        List of pattern strings for Bash tool matching
    """
    patterns = []
    for group, subcommands in sorted(commands.items()):
        if not subcommands:
            continue
        # Escape regex special chars in subcommands
        escaped = [_escape_regex(s) for s in subcommands]
        if len(escaped) == 1:
            patterns.append(f"{group} {escaped[0]}")
        else:
            alternation = "|".join(escaped)
            patterns.append(f"{group} ({alternation})")
    return patterns


def _escape_regex(s: str) -> str:
    """Escape regex special characters in a string.

    Args:
        s: String to escape

    Returns:
        Escaped string safe for use in regex alternation
    """
    special = r"\.^$*+?{}[]|()"
    result = []
    for c in s:
        if c in special:
            result.append(f"\\{c}")
        else:
            result.append(c)
    return "".join(result)


def export_claude_code(commands: dict[str, list[str]] | None = None,
                       file_patterns: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Export permissions in Claude Code format.

    Generates a JSON structure suitable for merging into .claude/settings.json.
    The output contains an 'allowedTools' list with Bash patterns for
    each command group.

    Note: This format is based on common IDE permission patterns. The exact
    format for Claude Code may differ - verify against Claude Code documentation
    at https://github.com/anthropics/claude-code if needed.

    Args:
        commands: Command config (defaults to get_commands_config())
        file_patterns: File patterns (defaults to get_file_patterns())

    Returns:
        Dictionary with 'allowedTools' key
    """
    if commands is None:
        commands = get_commands_config()
    if file_patterns is None:
        file_patterns = get_file_patterns()

    allowed_tools: list[dict[str, str]] = []

    # Add Bash patterns for command groups
    for pattern in build_bash_patterns(commands):
        allowed_tools.append({
            "tool": "Bash",
            "pattern": pattern,
        })

    return {"allowedTools": allowed_tools}


def format_summary(commands: dict[str, list[str]] | None = None) -> str:
    """Format a human-readable summary of required permissions.

    Args:
        commands: Command config (defaults to get_commands_config())

    Returns:
        Multi-line string summarizing required permissions
    """
    if commands is None:
        commands = get_commands_config()

    lines = []
    for group in sorted(commands.keys()):
        subcommands = commands[group]
        subcmd_str = ", ".join(subcommands)
        lines.append(f"  - {group} commands ({subcmd_str})")

    return "\n".join(lines)


def main() -> int:
    """CLI entry point for permissions export.

    Returns:
        Exit code (0 for success)
    """
    parser = argparse.ArgumentParser(
        description="Export Octopoid command permissions for IDE integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --format claude-code
  %(prog)s --format claude-code > .claude/octopoid-permissions.json
  %(prog)s --list
""",
    )

    parser.add_argument(
        "--format", "-f",
        choices=["claude-code"],
        help="Export permissions in IDE-specific format",
    )

    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="Show human-readable summary of required permissions",
    )

    args = parser.parse_args()

    if args.format:
        if args.format == "claude-code":
            result = export_claude_code()
            print(json.dumps(result, indent=2))
        return 0

    elif args.list:
        print("Octopoid agents require permission to run:")
        print()
        print(format_summary())
        print()
        print("To generate IDE permission config, run:")
        print("  orchestrator-permissions --format claude-code")
        return 0

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
