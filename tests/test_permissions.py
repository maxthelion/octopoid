"""Tests for the permissions module."""

import json
from unittest.mock import patch

import pytest

from orchestrator.config import (
    DEFAULT_COMMANDS,
    DEFAULT_FILE_PATTERNS,
    get_commands_config,
    get_file_patterns,
)
from orchestrator.permissions import (
    build_bash_patterns,
    export_claude_code,
    format_summary,
    main,
    _escape_regex,
)


class TestDefaultCommands:
    """Test that default command config is sensible."""

    def test_has_git_commands(self):
        """Default commands should include core git subcommands."""
        assert "git" in DEFAULT_COMMANDS
        git_cmds = DEFAULT_COMMANDS["git"]
        for expected in ["status", "fetch", "checkout", "commit", "push", "diff", "log"]:
            assert expected in git_cmds, f"Missing git subcommand: {expected}"

    def test_has_gh_commands(self):
        """Default commands should include GitHub CLI subcommands."""
        assert "gh" in DEFAULT_COMMANDS
        gh_cmds = DEFAULT_COMMANDS["gh"]
        assert "pr" in gh_cmds
        assert "issue" in gh_cmds

    def test_has_python_commands(self):
        """Default commands should include python subcommands."""
        assert "python" in DEFAULT_COMMANDS

    def test_has_npm_commands(self):
        """Default commands should include npm subcommands."""
        assert "npm" in DEFAULT_COMMANDS


class TestGetCommandsConfig:
    """Test config loading and merging."""

    def test_returns_defaults_when_no_config(self):
        """Should return defaults when agents.yaml has no commands section."""
        with patch("orchestrator.config.load_agents_config", return_value={}):
            result = get_commands_config()
            assert result == DEFAULT_COMMANDS

    def test_returns_defaults_when_config_missing(self):
        """Should return defaults when agents.yaml doesn't exist."""
        with patch("orchestrator.config.load_agents_config", side_effect=FileNotFoundError):
            result = get_commands_config()
            assert result == DEFAULT_COMMANDS

    def test_merges_user_commands(self):
        """User commands should extend defaults, not replace them."""
        user_config = {
            "commands": {
                "git": ["cherry-pick"],
                "cargo": ["build", "test"],
            }
        }
        with patch("orchestrator.config.load_agents_config", return_value=user_config):
            result = get_commands_config()
            # Original git commands still present
            assert "status" in result["git"]
            assert "fetch" in result["git"]
            # New git command added
            assert "cherry-pick" in result["git"]
            # New command group added
            assert "cargo" in result
            assert result["cargo"] == ["build", "test"]

    def test_no_duplicate_commands(self):
        """Adding an already-present command should not create duplicates."""
        user_config = {
            "commands": {
                "git": ["status", "fetch"],
            }
        }
        with patch("orchestrator.config.load_agents_config", return_value=user_config):
            result = get_commands_config()
            assert result["git"].count("status") == 1
            assert result["git"].count("fetch") == 1

    def test_does_not_mutate_defaults(self):
        """Merging should not mutate DEFAULT_COMMANDS."""
        original_git = list(DEFAULT_COMMANDS["git"])
        user_config = {
            "commands": {
                "git": ["cherry-pick"],
            }
        }
        with patch("orchestrator.config.load_agents_config", return_value=user_config):
            get_commands_config()
        assert DEFAULT_COMMANDS["git"] == original_git

    def test_ignores_non_list_values(self):
        """Non-list values in commands config should be ignored."""
        user_config = {
            "commands": {
                "git": "not-a-list",
            }
        }
        with patch("orchestrator.config.load_agents_config", return_value=user_config):
            result = get_commands_config()
            # git commands should be unchanged from defaults
            assert result["git"] == DEFAULT_COMMANDS["git"]


class TestGetFilePatterns:
    """Test file patterns config loading."""

    def test_returns_defaults_when_no_config(self):
        with patch("orchestrator.config.load_agents_config", return_value={}):
            result = get_file_patterns()
            assert result == DEFAULT_FILE_PATTERNS

    def test_merges_user_patterns(self):
        user_config = {
            "file_operations": {
                "read": ["*.rs"],
                "write": ["*.rs"],
            }
        }
        with patch("orchestrator.config.load_agents_config", return_value=user_config):
            result = get_file_patterns()
            assert "*.rs" in result["read"]
            assert "*.rs" in result["write"]
            # Defaults still present
            assert ".orchestrator/**/*" in result["read"]


class TestBuildBashPatterns:
    """Test Bash pattern generation."""

    def test_single_subcommand(self):
        """Single subcommand should produce simple pattern."""
        patterns = build_bash_patterns({"cargo": ["build"]})
        assert patterns == ["cargo build"]

    def test_multiple_subcommands(self):
        """Multiple subcommands should use regex alternation."""
        patterns = build_bash_patterns({"git": ["status", "fetch"]})
        assert len(patterns) == 1
        assert patterns[0] == "git (status|fetch)"

    def test_sorted_by_group(self):
        """Output should be sorted by command group name."""
        patterns = build_bash_patterns({
            "npm": ["install"],
            "git": ["status"],
            "cargo": ["build"],
        })
        assert patterns[0].startswith("cargo")
        assert patterns[1].startswith("git")
        assert patterns[2].startswith("npm")

    def test_empty_subcommands_skipped(self):
        """Groups with no subcommands should be skipped."""
        patterns = build_bash_patterns({"git": [], "npm": ["install"]})
        assert len(patterns) == 1
        assert patterns[0].startswith("npm")

    def test_special_chars_escaped(self):
        """Regex special chars in subcommands should be escaped."""
        patterns = build_bash_patterns({"tool": ["test.py"]})
        assert patterns == ["tool test\\.py"]


class TestEscapeRegex:
    """Test regex escaping helper."""

    def test_plain_string(self):
        assert _escape_regex("status") == "status"

    def test_dash_not_escaped(self):
        """Dashes are not regex special chars and should not be escaped."""
        assert _escape_regex("-m") == "-m"

    def test_dot(self):
        assert _escape_regex("test.py") == "test\\.py"

    def test_complex(self):
        assert _escape_regex("run test") == "run test"


class TestExportClaudeCode:
    """Test Claude Code format export."""

    def test_produces_allowed_tools(self):
        """Should produce a dict with allowedTools key."""
        result = export_claude_code(
            commands={"git": ["status"]},
            file_patterns={"read": [], "write": []},
        )
        assert "allowedTools" in result
        assert isinstance(result["allowedTools"], list)

    def test_bash_entries(self):
        """Should produce Bash tool entries for each command group."""
        result = export_claude_code(
            commands={"git": ["status", "fetch"], "npm": ["install"]},
            file_patterns={"read": [], "write": []},
        )
        tools = result["allowedTools"]
        bash_tools = [t for t in tools if t["tool"] == "Bash"]
        assert len(bash_tools) == 2  # git + npm

    def test_output_is_json_serializable(self):
        """Output should be JSON-serializable."""
        result = export_claude_code(
            commands=DEFAULT_COMMANDS,
            file_patterns=DEFAULT_FILE_PATTERNS,
        )
        serialized = json.dumps(result)
        assert json.loads(serialized) == result


class TestFormatSummary:
    """Test human-readable summary formatting."""

    def test_includes_all_groups(self):
        """Summary should mention all command groups."""
        summary = format_summary({"git": ["status"], "npm": ["install"]})
        assert "git" in summary
        assert "npm" in summary

    def test_includes_subcommands(self):
        """Summary should list subcommands."""
        summary = format_summary({"git": ["status", "fetch"]})
        assert "status" in summary
        assert "fetch" in summary

    def test_with_defaults(self):
        """Should work with default commands."""
        summary = format_summary(DEFAULT_COMMANDS)
        assert "git" in summary
        assert "gh" in summary


class TestCLI:
    """Test CLI entry point."""

    def test_export_claude_code(self, capsys):
        """Export command should output valid JSON."""
        with patch(
            "sys.argv",
            ["orchestrator-permissions", "--format", "claude-code"],
        ):
            with patch("orchestrator.permissions.get_commands_config", return_value={"git": ["status"]}):
                with patch("orchestrator.permissions.get_file_patterns", return_value={"read": [], "write": []}):
                    exit_code = main()
        assert exit_code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert "allowedTools" in result

    def test_list(self, capsys):
        """List command should output readable text."""
        with patch(
            "sys.argv",
            ["orchestrator-permissions", "--list"],
        ):
            with patch("orchestrator.permissions.get_commands_config", return_value={"git": ["status"]}):
                exit_code = main()
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "git" in captured.out

    def test_no_args_shows_help(self, capsys):
        """No arguments should show help and return 1."""
        with patch("sys.argv", ["orchestrator-permissions"]):
            exit_code = main()
        assert exit_code == 1
