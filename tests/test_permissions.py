"""Tests for orchestrator.permissions module."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestLoadCommandsConfig:
    """Tests for load_commands_config function."""

    def test_load_with_valid_config(self, mock_orchestrator_dir):
        """Test loading commands from a valid config file."""
        config_path = mock_orchestrator_dir / ".octopoid" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("""
mode: local
commands:
  git:
    - status
    - commit
  npm:
    - install
""")

        with patch('orchestrator.permissions.find_parent_project', return_value=mock_orchestrator_dir):
            from orchestrator.permissions import load_commands_config

            commands = load_commands_config()

            assert commands["git"] == ["status", "commit"]
            assert commands["npm"] == ["install"]

    def test_load_without_commands_section(self, mock_orchestrator_dir):
        """Test loading config without commands section returns defaults."""
        config_path = mock_orchestrator_dir / ".octopoid" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("mode: local\n")

        with patch('orchestrator.permissions.find_parent_project', return_value=mock_orchestrator_dir):
            from orchestrator.permissions import load_commands_config, get_default_commands

            commands = load_commands_config()
            defaults = get_default_commands()

            assert commands == defaults

    def test_load_without_config_file(self, mock_orchestrator_dir):
        """Test loading when config file doesn't exist returns defaults."""
        with patch('orchestrator.permissions.find_parent_project', return_value=mock_orchestrator_dir):
            from orchestrator.permissions import load_commands_config, get_default_commands

            commands = load_commands_config()
            defaults = get_default_commands()

            assert commands == defaults

    def test_load_with_invalid_yaml(self, mock_orchestrator_dir):
        """Test loading with invalid YAML returns defaults."""
        config_path = mock_orchestrator_dir / ".octopoid" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("invalid: yaml: content: [[[")

        with patch('orchestrator.permissions.find_parent_project', return_value=mock_orchestrator_dir):
            from orchestrator.permissions import load_commands_config, get_default_commands

            commands = load_commands_config()
            defaults = get_default_commands()

            assert commands == defaults


class TestGetDefaultCommands:
    """Tests for get_default_commands function."""

    def test_returns_expected_structure(self):
        """Test that default commands have expected structure."""
        from orchestrator.permissions import get_default_commands

        defaults = get_default_commands()

        assert "git" in defaults
        assert "npm" in defaults
        assert "npx" in defaults
        assert "file_operations" in defaults
        assert isinstance(defaults["git"], list)
        assert isinstance(defaults["npm"], list)
        assert isinstance(defaults["npx"], list)
        assert isinstance(defaults["file_operations"], dict)

    def test_git_commands_included(self):
        """Test that common git commands are included."""
        from orchestrator.permissions import get_default_commands

        defaults = get_default_commands()
        git_cmds = defaults["git"]

        assert "status" in git_cmds
        assert "commit" in git_cmds
        assert "push" in git_cmds
        assert "fetch" in git_cmds

    def test_file_operations_structure(self):
        """Test that file operations have read and write patterns."""
        from orchestrator.permissions import get_default_commands

        defaults = get_default_commands()
        file_ops = defaults["file_operations"]

        assert "read" in file_ops
        assert "write" in file_ops
        assert isinstance(file_ops["read"], list)
        assert isinstance(file_ops["write"], list)


class TestExportClaudeCode:
    """Tests for export_claude_code function."""

    def test_export_basic_structure(self):
        """Test that export produces correct structure."""
        from orchestrator.permissions import export_claude_code

        commands = {
            "git": ["status", "commit"],
            "npm": ["install"],
        }

        result = export_claude_code(commands)

        assert "allowedCommands" in result
        assert isinstance(result["allowedCommands"], list)

    def test_export_git_commands(self):
        """Test that git commands are exported correctly."""
        from orchestrator.permissions import export_claude_code

        commands = {
            "git": ["status", "commit", "push"],
        }

        result = export_claude_code(commands)
        allowed = result["allowedCommands"]

        # Find the git command entry
        git_entry = next((cmd for cmd in allowed if cmd["tool"] == "Bash" and "git" in cmd["pattern"]), None)
        assert git_entry is not None
        assert git_entry["pattern"] == "git (status|commit|push)"

    def test_export_npm_commands(self):
        """Test that npm commands are exported correctly."""
        from orchestrator.permissions import export_claude_code

        commands = {
            "npm": ["install", "run test"],
        }

        result = export_claude_code(commands)
        allowed = result["allowedCommands"]

        npm_entry = next((cmd for cmd in allowed if cmd["tool"] == "Bash" and "npm" in cmd["pattern"]), None)
        assert npm_entry is not None
        assert npm_entry["pattern"] == "npm (install|run test)"

    def test_export_file_operations(self):
        """Test that file operations are exported correctly."""
        from orchestrator.permissions import export_claude_code

        commands = {
            "file_operations": {
                "read": ["src/**", "tests/**"],
                "write": ["src/**"],
            }
        }

        result = export_claude_code(commands)
        allowed = result["allowedCommands"]

        # Check for Read tool entry
        read_entry = next((cmd for cmd in allowed if cmd["tool"] == "Read"), None)
        assert read_entry is not None
        # Pattern should convert ** to .* (replacing the globstar pattern)
        # The implementation uses .replace("**", ".*") which gives us "src/.*"
        assert "src/" in read_entry["pattern"]
        assert "tests/" in read_entry["pattern"]

        # Check for Write tool entry
        write_entry = next((cmd for cmd in allowed if cmd["tool"] == "Write"), None)
        assert write_entry is not None
        assert "src/" in write_entry["pattern"]

        # Check for Edit tool entry
        edit_entry = next((cmd for cmd in allowed if cmd["tool"] == "Edit"), None)
        assert edit_entry is not None

    def test_export_empty_commands(self):
        """Test export with empty commands."""
        from orchestrator.permissions import export_claude_code

        commands = {}
        result = export_claude_code(commands)

        assert result["allowedCommands"] == []

    def test_export_npx_commands(self):
        """Test that npx commands are exported correctly."""
        from orchestrator.permissions import export_claude_code

        commands = {
            "npx": ["vitest run", "tsc --noEmit"],
        }

        result = export_claude_code(commands)
        allowed = result["allowedCommands"]

        npx_entry = next((cmd for cmd in allowed if cmd["tool"] == "Bash" and "npx" in cmd["pattern"]), None)
        assert npx_entry is not None
        assert npx_entry["pattern"] == "npx (vitest run|tsc --noEmit)"


class TestExportPermissions:
    """Tests for export_permissions function."""

    def test_export_claude_code_format(self, mock_orchestrator_dir):
        """Test exporting in claude-code format."""
        with patch('orchestrator.permissions.load_commands_config') as mock_load:
            mock_load.return_value = {
                "git": ["status"],
                "npm": ["install"],
            }

            from orchestrator.permissions import export_permissions

            result = export_permissions(format="claude-code")

            # Should be valid JSON
            parsed = json.loads(result)
            assert "allowedCommands" in parsed

    def test_export_unknown_format(self):
        """Test that unknown format raises ValueError."""
        from orchestrator.permissions import export_permissions

        with pytest.raises(ValueError, match="Unknown format"):
            # This should fail because we removed cursor/windsurf
            # and the type system only allows "claude-code"
            # But to test the runtime check:
            export_permissions(format="unknown")  # type: ignore

    def test_export_returns_formatted_json(self):
        """Test that export returns formatted JSON string."""
        with patch('orchestrator.permissions.load_commands_config') as mock_load:
            mock_load.return_value = {"git": ["status"]}

            from orchestrator.permissions import export_permissions

            result = export_permissions(format="claude-code")

            # Should be indented JSON
            assert result.startswith("{\n")
            assert "  " in result  # Has indentation


class TestGetPermissionsSummary:
    """Tests for get_permissions_summary function."""

    def test_summary_with_all_commands(self):
        """Test summary with all command types."""
        with patch('orchestrator.permissions.load_commands_config') as mock_load:
            mock_load.return_value = {
                "git": ["status", "commit"],
                "npm": ["install"],
                "npx": ["vitest run"],
                "file_operations": {
                    "read": ["src/**"],
                    "write": ["src/**"],
                }
            }

            from orchestrator.permissions import get_permissions_summary

            summary = get_permissions_summary()

            assert any("git" in line.lower() for line in summary)
            assert any("npm" in line.lower() for line in summary)
            assert any("npx" in line.lower() for line in summary)
            assert any("read" in line.lower() for line in summary)
            assert any("write" in line.lower() for line in summary)

    def test_summary_with_partial_commands(self):
        """Test summary with only some command types."""
        with patch('orchestrator.permissions.load_commands_config') as mock_load:
            mock_load.return_value = {
                "git": ["status"],
            }

            from orchestrator.permissions import get_permissions_summary

            summary = get_permissions_summary()

            assert len(summary) == 1
            assert "git" in summary[0].lower()

    def test_summary_empty_commands(self):
        """Test summary with no commands."""
        with patch('orchestrator.permissions.load_commands_config') as mock_load:
            mock_load.return_value = {}

            from orchestrator.permissions import get_permissions_summary

            summary = get_permissions_summary()

            assert summary == []
