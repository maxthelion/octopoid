"""Command whitelist management for IDE permission systems.

This module provides functionality to export command whitelists in formats
suitable for IDE permission systems like Claude Code, Cursor, and Windsurf.
"""

import json
from pathlib import Path
from typing import Any, Literal

import yaml

from orchestrator.config import find_parent_project

PermissionFormat = Literal["claude-code", "cursor", "windsurf"]


def load_commands_config() -> dict[str, Any]:
    """Load command whitelist configuration from .octopoid/config.yaml.

    Returns:
        Dictionary with command definitions, or default config if not specified
    """
    try:
        config_path = find_parent_project() / ".octopoid" / "config.yaml"
        if not config_path.exists():
            return get_default_commands()

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        commands = config.get("commands")
        if commands and isinstance(commands, dict):
            return commands

        return get_default_commands()
    except Exception:
        return get_default_commands()


def get_default_commands() -> dict[str, Any]:
    """Get default command whitelist for typical Octopoid setup.

    Returns:
        Dictionary with default git, npm, npx, and file operation commands
    """
    return {
        "git": [
            "status",
            "fetch",
            "checkout",
            "branch",
            "commit",
            "push",
            "rebase",
            "merge",
            "diff",
            "log",
            "rev-list",
            "ls-remote",
            "worktree",
            "submodule",
        ],
        "npm": [
            "run test",
            "run build",
            "run dev",
            "install",
        ],
        "npx": [
            "vitest run",
            "tsc --noEmit",
        ],
        "file_operations": {
            "read": [
                ".octopoid/**",
                "src/**",
                "tests/**",
                "package.json",
                "tsconfig.json",
                "*.config.*",
                "README.md",
                "docs/**",
            ],
            "write": [
                ".octopoid/**",
                "src/**",
                "tests/**",
                "docs/**",
            ],
        },
    }


def export_claude_code(commands: dict[str, Any]) -> dict[str, Any]:
    """Export commands in Claude Code format.

    Args:
        commands: Command whitelist configuration

    Returns:
        Dictionary suitable for Claude Code .claude/settings.json
    """
    allowed_commands = []

    # Git commands
    git_cmds = commands.get("git", [])
    if git_cmds:
        git_pattern = "|".join(git_cmds)
        allowed_commands.append({
            "tool": "Bash",
            "pattern": f"git ({git_pattern})"
        })

    # npm commands
    npm_cmds = commands.get("npm", [])
    if npm_cmds:
        npm_pattern = "|".join(npm_cmds)
        allowed_commands.append({
            "tool": "Bash",
            "pattern": f"npm ({npm_pattern})"
        })

    # npx commands
    npx_cmds = commands.get("npx", [])
    if npx_cmds:
        npx_pattern = "|".join(npx_cmds)
        allowed_commands.append({
            "tool": "Bash",
            "pattern": f"npx ({npx_pattern})"
        })

    # File operations
    file_ops = commands.get("file_operations", {})
    read_patterns = file_ops.get("read", [])
    write_patterns = file_ops.get("write", [])

    if read_patterns:
        # Convert glob patterns to regex patterns for Read tool
        read_pattern = "|".join(p.replace("**", ".*").replace("*", "[^/]*") for p in read_patterns)
        allowed_commands.append({
            "tool": "Read",
            "pattern": f"({read_pattern})"
        })

    if write_patterns:
        # Convert glob patterns to regex patterns for Write/Edit tools
        write_pattern = "|".join(p.replace("**", ".*").replace("*", "[^/]*") for p in write_patterns)
        allowed_commands.append({
            "tool": "Write",
            "pattern": f"({write_pattern})"
        })
        allowed_commands.append({
            "tool": "Edit",
            "pattern": f"({write_pattern})"
        })

    return {"allowedCommands": allowed_commands}


def export_cursor(commands: dict[str, Any]) -> dict[str, Any]:
    """Export commands in Cursor format.

    Args:
        commands: Command whitelist configuration

    Returns:
        Dictionary suitable for Cursor configuration
    """
    # Cursor uses similar format to Claude Code for now
    # This can be customized if Cursor has different requirements
    return export_claude_code(commands)


def export_windsurf(commands: dict[str, Any]) -> dict[str, Any]:
    """Export commands in Windsurf format.

    Args:
        commands: Command whitelist configuration

    Returns:
        Dictionary suitable for Windsurf configuration
    """
    # Windsurf uses similar format to Claude Code for now
    # This can be customized if Windsurf has different requirements
    return export_claude_code(commands)


def export_permissions(format: PermissionFormat = "claude-code") -> str:
    """Export command whitelist in the specified IDE format.

    Args:
        format: Target IDE format (claude-code, cursor, windsurf)

    Returns:
        JSON string with permissions configuration
    """
    commands = load_commands_config()

    if format == "claude-code":
        result = export_claude_code(commands)
    elif format == "cursor":
        result = export_cursor(commands)
    elif format == "windsurf":
        result = export_windsurf(commands)
    else:
        raise ValueError(f"Unknown format: {format}")

    return json.dumps(result, indent=2)


def get_permissions_summary() -> list[str]:
    """Get a human-readable summary of configured permissions.

    Returns:
        List of strings describing the configured commands
    """
    commands = load_commands_config()
    summary = []

    git_cmds = commands.get("git", [])
    if git_cmds:
        summary.append(f"Git commands: {', '.join(git_cmds)}")

    npm_cmds = commands.get("npm", [])
    if npm_cmds:
        summary.append(f"npm commands: {', '.join(npm_cmds)}")

    npx_cmds = commands.get("npx", [])
    if npx_cmds:
        summary.append(f"npx commands: {', '.join(npx_cmds)}")

    file_ops = commands.get("file_operations", {})
    read_patterns = file_ops.get("read", [])
    write_patterns = file_ops.get("write", [])

    if read_patterns:
        summary.append(f"Read file patterns: {', '.join(read_patterns)}")
    if write_patterns:
        summary.append(f"Write file patterns: {', '.join(write_patterns)}")

    return summary
