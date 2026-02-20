"""Tests for init.py UX improvements."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.init import init_orchestrator, main


@pytest.fixture
def mock_project(tmp_path):
    """Create a mock project directory with .git."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    # Create a minimal orchestrator submodule structure
    submodule = tmp_path / "orchestrator"
    submodule.mkdir()
    (submodule / "orchestrator").mkdir()
    (submodule / "commands" / "management").mkdir(parents=True)

    # Create some fake management skill files
    for name in ["enqueue", "queue-status", "agent-status"]:
        (submodule / "commands" / "management" / f"{name}.md").write_text(f"# {name}")

    # Create a fake init.py so find_parent_project works
    init_py = submodule / "orchestrator" / "init.py"
    init_py.write_text("# stub")

    return tmp_path, submodule


class TestInitWelcomeMessage:
    """Tests for the welcome message shown at init start."""

    def test_welcome_message_displayed(self, mock_project, capsys):
        """Init shows a welcome message with project path."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "Welcome to Octopoid!" in output
        assert str(project_dir) in output

    def test_project_description_shown(self, mock_project, capsys):
        """Init shows a description of what octopoid is."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "API-driven orchestrator" in output


class TestInitDirectoryCreation:
    """Tests for directory creation output."""

    def test_fresh_init_reports_created_count(self, mock_project, capsys):
        """Fresh init reports how many directories were created."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "Created .octopoid/ directory structure" in output
        assert "directories" in output

    def test_second_init_reports_existing(self, mock_project, capsys):
        """Second init reports directories already exist."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                # Run init twice
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )
                # Clear captured output from first run
                capsys.readouterr()

                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "already exists" in output

    def test_agents_yaml_created_message(self, mock_project, capsys):
        """Reports agents.yaml creation on fresh init."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "Created .octopoid/agents.yaml" in output

    def test_existing_agents_yaml_preserved(self, mock_project, capsys):
        """Existing agents.yaml is not overwritten."""
        project_dir, submodule = mock_project

        # Pre-create agents.yaml
        orchestrator_dir = project_dir / ".octopoid"
        orchestrator_dir.mkdir(parents=True, exist_ok=True)
        agents_yaml = orchestrator_dir / "agents.yaml"
        agents_yaml.write_text("# custom config")

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "Using existing .octopoid/agents.yaml" in output
        assert agents_yaml.read_text() == "# custom config"


class TestInitNextSteps:
    """Tests for the post-init next steps guidance."""

    def test_next_steps_shown(self, mock_project, capsys):
        """Init shows next steps after setup."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "Next steps:" in output

    def test_claude_md_instructions_shown(self, mock_project, capsys):
        """Next steps includes CLAUDE.md setup instructions."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "CLAUDE.md" in output
        assert ".agent-instructions.md" in output

    def test_scheduler_command_shown(self, mock_project, capsys):
        """Next steps includes how to start the scheduler."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "scheduler.py" in output
        assert "Run once" in output

    def test_enqueue_command_shown(self, mock_project, capsys):
        """Next steps includes how to create tasks."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "/enqueue" in output

    def test_status_commands_shown(self, mock_project, capsys):
        """Next steps includes status check commands."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "/queue-status" in output
        assert "/agent-status" in output

    def test_documentation_reference_shown(self, mock_project, capsys):
        """Next steps includes documentation reference."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "Documentation:" in output
        assert "README.md" in output

    def test_setup_complete_banner(self, mock_project, capsys):
        """Shows a clear 'setup complete' banner."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "Setup complete!" in output


class TestInitSkillInstallation:
    """Tests for skill installation output."""

    def test_skills_installed_count(self, mock_project, capsys):
        """Reports how many skills were installed."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=True,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "Installed 3 management skills" in output

    def test_skills_command_names_shown(self, mock_project, capsys):
        """Lists installed skill command names."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=True,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "/enqueue" in output
        assert "/queue-status" in output

    def test_skills_skipped_shows_hint(self, mock_project, capsys):
        """Skipping skills shows how to install them later."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "Skipping skill installation" in output
        assert "install-commands" in output


class TestInitGitignore:
    """Tests for .gitignore update output."""

    def test_gitignore_entries_added_count(self, mock_project, capsys):
        """Reports how many gitignore entries were added."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=True,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "Updated .gitignore" in output
        assert "entries added" in output

    def test_gitignore_skipped_shows_hint(self, mock_project, capsys):
        """Skipping gitignore shows how to add entries later."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "Skipping .gitignore update" in output
        assert "--gitignore" in output


class TestInitModeSelection:
    """Tests for mode display in output."""

    def test_default_mode_shown_in_output(self, mock_project, capsys):
        """Running init without mode flag shows 'local' mode."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                )

        output = capsys.readouterr().out
        assert "Mode:    local" in output

    def test_explicit_local_mode_shown(self, mock_project, capsys):
        """Passing mode='local' explicitly shows in output."""
        project_dir, submodule = mock_project

        with patch("orchestrator.init.find_parent_project", return_value=project_dir):
            with patch("orchestrator.init.get_orchestrator_submodule", return_value=submodule):
                init_orchestrator(
                    install_skills=False,
                    update_gitignore=False,
                    non_interactive=True,
                    mode="local",
                )

        output = capsys.readouterr().out
        assert "Mode:    local" in output
