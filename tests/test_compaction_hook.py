"""Tests for the PreCompact hook configuration and script behavior."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

# Path to the project root (parent of orchestrator/)
PROJECT_ROOT = Path(__file__).parent.parent.parent


class TestHookConfig:
    """Validate the hook configuration in .claude/settings.json."""

    def test_settings_json_is_valid(self):
        """settings.json should be valid JSON."""
        settings_path = PROJECT_ROOT / ".claude" / "settings.json"
        assert settings_path.exists(), f"settings.json not found at {settings_path}"
        with open(settings_path) as f:
            data = json.load(f)
        assert "hooks" in data

    def test_precompact_hook_exists(self):
        """PreCompact hook should be defined in settings.json."""
        settings_path = PROJECT_ROOT / ".claude" / "settings.json"
        with open(settings_path) as f:
            data = json.load(f)
        assert "PreCompact" in data["hooks"], "PreCompact hook not configured"

    def test_precompact_hook_references_correct_script(self):
        """PreCompact hook command should reference the checkpoint script."""
        settings_path = PROJECT_ROOT / ".claude" / "settings.json"
        with open(settings_path) as f:
            data = json.load(f)

        hooks = data["hooks"]["PreCompact"]
        assert len(hooks) > 0, "PreCompact should have at least one hook group"

        hook_group = hooks[0]
        hook = hook_group["hooks"][0]
        assert hook["type"] == "command"
        assert "write-compaction-checkpoint.sh" in hook["command"]

    def test_precompact_hook_has_timeout(self):
        """PreCompact hook should have a timeout to avoid blocking compaction."""
        settings_path = PROJECT_ROOT / ".claude" / "settings.json"
        with open(settings_path) as f:
            data = json.load(f)

        hook = data["hooks"]["PreCompact"][0]["hooks"][0]
        assert "timeout" in hook, "PreCompact hook should have a timeout"
        assert hook["timeout"] <= 30000, "Timeout should be <= 30 seconds"

    def test_hook_script_exists(self):
        """The checkpoint script file should exist."""
        script_path = PROJECT_ROOT / ".claude" / "hooks" / "write-compaction-checkpoint.sh"
        assert script_path.exists(), f"Hook script not found at {script_path}"

    def test_hook_script_is_executable(self):
        """The checkpoint script should be executable."""
        script_path = PROJECT_ROOT / ".claude" / "hooks" / "write-compaction-checkpoint.sh"
        assert os.access(script_path, os.X_OK), "Hook script should be executable"

    def test_hook_script_has_shebang(self):
        """The checkpoint script should start with a bash shebang."""
        script_path = PROJECT_ROOT / ".claude" / "hooks" / "write-compaction-checkpoint.sh"
        with open(script_path) as f:
            first_line = f.readline().strip()
        assert first_line == "#!/bin/bash", f"Expected bash shebang, got: {first_line}"


class TestHookScriptBehavior:
    """Test the hook script's behavior in various scenarios."""

    def test_noop_without_agent_name(self, tmp_path):
        """Hook should exit 0 silently when AGENT_NAME is not set."""
        script_path = PROJECT_ROOT / ".claude" / "hooks" / "write-compaction-checkpoint.sh"
        env = os.environ.copy()
        # Ensure AGENT_NAME is not set
        env.pop("AGENT_NAME", None)

        result = subprocess.run(
            [str(script_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_noop_without_task_id(self, tmp_path):
        """Hook should exit 0 when no task ID is available."""
        script_path = PROJECT_ROOT / ".claude" / "hooks" / "write-compaction-checkpoint.sh"

        # Set up minimal env with AGENT_NAME but no task info
        env = os.environ.copy()
        env["AGENT_NAME"] = "test-agent"
        env["ORCHESTRATOR_DIR"] = str(tmp_path / "orch")
        env["SHARED_DIR"] = str(tmp_path / "shared")
        env.pop("CURRENT_TASK_ID", None)

        # Create agent dir but no state.json (so no task ID source)
        (tmp_path / "orch" / "agents" / "test-agent").mkdir(parents=True)

        result = subprocess.run(
            [str(script_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0

    def test_writes_checkpoint_with_task_id(self, tmp_path):
        """Hook should write a checkpoint when task ID is available."""
        script_path = PROJECT_ROOT / ".claude" / "hooks" / "write-compaction-checkpoint.sh"

        # Set up env
        env = os.environ.copy()
        env["AGENT_NAME"] = "test-agent"
        env["CURRENT_TASK_ID"] = "abc12345"
        env["ORCHESTRATOR_DIR"] = str(tmp_path / "orch")
        env["SHARED_DIR"] = str(tmp_path / "shared")
        # Remove PATH entries that might find `claude` to keep test fast
        # The script will fall back to raw log capture when claude fails
        env["PATH"] = "/usr/bin:/bin"

        # Create agent dir with a stdout.log
        agent_dir = tmp_path / "orch" / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "stdout.log").write_text("Working on feature X\nImplemented function Y\n")

        # Create notes dir
        notes_dir = tmp_path / "shared" / "notes"
        notes_dir.mkdir(parents=True)

        result = subprocess.run(
            [str(script_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

        # Check that notes file was created
        notes_file = notes_dir / "TASK-abc12345.md"
        assert notes_file.exists(), "Notes file should be created"
        content = notes_file.read_text()
        assert "## Checkpoint" in content

    def test_reads_task_id_from_state_json(self, tmp_path):
        """Hook should fall back to reading task ID from state.json."""
        script_path = PROJECT_ROOT / ".claude" / "hooks" / "write-compaction-checkpoint.sh"

        env = os.environ.copy()
        env["AGENT_NAME"] = "test-agent"
        env.pop("CURRENT_TASK_ID", None)  # No CURRENT_TASK_ID
        env["ORCHESTRATOR_DIR"] = str(tmp_path / "orch")
        env["SHARED_DIR"] = str(tmp_path / "shared")
        env["PATH"] = "/usr/bin:/bin"

        # Create state.json with current_task
        agent_dir = tmp_path / "orch" / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        state = {"running": True, "current_task": "def67890"}
        (agent_dir / "state.json").write_text(json.dumps(state))
        (agent_dir / "stdout.log").write_text("some output\n")

        notes_dir = tmp_path / "shared" / "notes"
        notes_dir.mkdir(parents=True)

        result = subprocess.run(
            [str(script_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

        notes_file = notes_dir / "TASK-def67890.md"
        assert notes_file.exists(), "Notes file should be created using task ID from state.json"

    def test_checkpoint_without_stdout_log(self, tmp_path):
        """Hook should write a minimal checkpoint when no stdout.log exists."""
        script_path = PROJECT_ROOT / ".claude" / "hooks" / "write-compaction-checkpoint.sh"

        env = os.environ.copy()
        env["AGENT_NAME"] = "test-agent"
        env["CURRENT_TASK_ID"] = "nolog123"
        env["ORCHESTRATOR_DIR"] = str(tmp_path / "orch")
        env["SHARED_DIR"] = str(tmp_path / "shared")
        env["PATH"] = "/usr/bin:/bin"

        # Create agent dir but NO stdout.log
        agent_dir = tmp_path / "orch" / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)

        notes_dir = tmp_path / "shared" / "notes"
        notes_dir.mkdir(parents=True)

        result = subprocess.run(
            [str(script_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0

        notes_file = notes_dir / "TASK-nolog123.md"
        assert notes_file.exists()
        content = notes_file.read_text()
        assert "no log content available" in content


class TestImplementerSetsTaskId:
    """Test that the implementer role sets current_task_id before invoke_claude."""

    @patch("orchestrator.roles.implementer.get_current_branch", return_value="main")
    def test_implementer_sets_current_task_id(self, _mock_branch):
        """ImplementerRole should set current_task_id after claiming a task."""
        import os
        from unittest.mock import MagicMock, patch

        env = {
            "AGENT_NAME": "impl-test",
            "AGENT_ID": "1",
            "AGENT_ROLE": "implementer",
            "PARENT_PROJECT": "/tmp/project",
            "WORKTREE": "/tmp/worktree",
            "SHARED_DIR": "/tmp/shared",
            "ORCHESTRATOR_DIR": "/tmp/orch",
        }

        with patch.dict(os.environ, env):
            from orchestrator.roles.implementer import ImplementerRole

            role = ImplementerRole()

            mock_task = {
                "id": "test123",
                "title": "Test task",
                "branch": "main",
                "path": "/tmp/task.md",
                "content": "Do the thing",
            }

            with (
                patch("orchestrator.roles.implementer.claim_task", return_value=mock_task),
                patch("orchestrator.roles.implementer.create_feature_branch", return_value="agent/test"),
                patch("orchestrator.roles.implementer.get_head_ref", return_value="abc123"),
                patch("orchestrator.roles.implementer.get_notes_dir", return_value=Path("/tmp/notes")),
                patch("orchestrator.roles.implementer.get_review_feedback", return_value=None),
                patch("orchestrator.roles.implementer.get_task_notes", return_value=None),
                patch.object(role, "invoke_claude", return_value=(0, "done", "")) as mock_invoke,
                patch("orchestrator.roles.implementer.get_commit_count", return_value=1),
                patch("orchestrator.roles.implementer.save_task_notes"),
                patch("orchestrator.roles.implementer.has_uncommitted_changes", return_value=False),
                patch("orchestrator.roles.implementer.create_pull_request", return_value="https://pr"),
                patch("orchestrator.roles.implementer.is_db_enabled", return_value=False),
                patch("orchestrator.roles.implementer.complete_task"),
                patch.object(role, "_check_for_continuation_work", return_value=None),
            ):
                role.run()

                # Verify current_task_id was set before invoke_claude
                assert role.current_task_id == "test123"

                # Verify invoke_claude was called (it would have CURRENT_TASK_ID in env)
                assert mock_invoke.called
