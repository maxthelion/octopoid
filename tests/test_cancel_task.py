"""Tests for cancel_task() — verifies kill, worktree removal, runtime removal, server delete."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from octopoid.pool import load_blueprint_pids, save_blueprint_pids


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ALIVE_PID = 88888
TASK_ID = "abc12345"


@pytest.fixture()
def runtime_dirs(tmp_path, monkeypatch):
    """Set up temp runtime directories and patch config."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()

    # pool.py uses get_agents_runtime_dir at module level for load/save
    monkeypatch.setattr("octopoid.pool.get_agents_runtime_dir", lambda: agents_dir)
    # cancel_task does lazy imports from config — patch the source module
    monkeypatch.setattr("octopoid.config.get_agents_runtime_dir", lambda: agents_dir)
    monkeypatch.setattr("octopoid.config.get_tasks_dir", lambda: tasks_dir)

    return agents_dir, tasks_dir


@pytest.fixture()
def task_dir_with_worktree(runtime_dirs):
    """Create a minimal task runtime dir with a fake worktree."""
    agents_dir, tasks_dir = runtime_dirs
    task_dir = tasks_dir / TASK_ID
    task_dir.mkdir()
    worktree = task_dir / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: ../../.git/worktrees/abc12345\n")
    (task_dir / "stdout.log").write_text("Agent output\n")
    return task_dir


@pytest.fixture()
def running_agent(runtime_dirs):
    """Register a running agent PID for the task."""
    agents_dir, tasks_dir = runtime_dirs
    bp_dir = agents_dir / "implementer"
    bp_dir.mkdir()
    save_blueprint_pids("implementer", {
        ALIVE_PID: {
            "task_id": TASK_ID,
            "started_at": "2026-02-27T10:00:00+00:00",
            "instance_name": "implementer-1",
        }
    })


# ---------------------------------------------------------------------------
# Test: cancel_task kills agent process
# ---------------------------------------------------------------------------


class TestCancelTaskKillsAgent:
    def test_kills_running_agent_by_task_id(self, runtime_dirs, running_agent):
        """cancel_task kills the agent process and reports the PID."""
        from octopoid.tasks import cancel_task

        kill_calls = []

        def fake_killpg(pgid, sig):
            kill_calls.append(("killpg", pgid, sig))

        def fake_getpgid(pid):
            return pid + 1000

        mock_sdk = MagicMock()
        mock_sdk.tasks.delete = MagicMock()

        with patch("octopoid.tasks.get_sdk", return_value=mock_sdk), \
             patch("os.killpg", side_effect=fake_killpg), \
             patch("os.getpgid", side_effect=fake_getpgid), \
             patch("octopoid.git_utils._remove_worktree", return_value=None):

            result = cancel_task(TASK_ID)

        assert result["killed_pid"] == ALIVE_PID
        assert len(kill_calls) == 1
        assert kill_calls[0][0] == "killpg"

    def test_pid_removed_from_tracking_after_cancel(self, runtime_dirs, running_agent):
        """After cancel_task, the PID is removed from running_pids.json."""
        from octopoid.tasks import cancel_task

        mock_sdk = MagicMock()

        with patch("octopoid.tasks.get_sdk", return_value=mock_sdk), \
             patch("os.killpg", side_effect=OSError("no permission")), \
             patch("os.kill", return_value=None), \
             patch("os.getpgid", side_effect=OSError), \
             patch("octopoid.git_utils._remove_worktree", return_value=None):

            cancel_task(TASK_ID)

        pids = load_blueprint_pids("implementer")
        assert ALIVE_PID not in pids

    def test_no_agent_running_returns_none_killed_pid(self, runtime_dirs):
        """If no agent is running for the task, killed_pid is None."""
        from octopoid.tasks import cancel_task

        mock_sdk = MagicMock()

        with patch("octopoid.tasks.get_sdk", return_value=mock_sdk):
            result = cancel_task(TASK_ID)

        assert result["killed_pid"] is None

    def test_process_already_dead_does_not_error(self, runtime_dirs, running_agent):
        """If the process is already dead, kill errors are swallowed gracefully."""
        from octopoid.tasks import cancel_task

        mock_sdk = MagicMock()

        with patch("octopoid.tasks.get_sdk", return_value=mock_sdk), \
             patch("os.killpg", side_effect=ProcessLookupError), \
             patch("os.kill", side_effect=ProcessLookupError), \
             patch("os.getpgid", side_effect=ProcessLookupError), \
             patch("octopoid.git_utils._remove_worktree", return_value=None):

            result = cancel_task(TASK_ID)

        # PID was found and kill was attempted
        assert result["killed_pid"] == ALIVE_PID
        # No kill-related errors expected (all kill failures are swallowed)
        kill_errors = [e for e in result["errors"] if "kill" in e.lower()]
        assert not kill_errors


# ---------------------------------------------------------------------------
# Test: cancel_task removes worktree and runtime dir
# ---------------------------------------------------------------------------


class TestCancelTaskRemovesFiles:
    def test_removes_worktree_via_git(self, runtime_dirs, task_dir_with_worktree):
        """cancel_task calls _remove_worktree to clean up the git worktree."""
        from octopoid.tasks import cancel_task

        agents_dir, tasks_dir = runtime_dirs
        mock_sdk = MagicMock()
        remove_calls = []

        def fake_remove_worktree(parent_repo, worktree_path):
            remove_calls.append(worktree_path)
            import shutil
            shutil.rmtree(worktree_path, ignore_errors=True)

        with patch("octopoid.tasks.get_sdk", return_value=mock_sdk), \
             patch("octopoid.git_utils._remove_worktree", side_effect=fake_remove_worktree), \
             patch("octopoid.config.find_parent_project", return_value=Path("/fake/repo")):

            result = cancel_task(TASK_ID)

        assert result["worktree_removed"] is True
        assert len(remove_calls) == 1
        assert remove_calls[0] == tasks_dir / TASK_ID / "worktree"

    def test_removes_runtime_dir(self, runtime_dirs, task_dir_with_worktree):
        """cancel_task removes the entire task runtime directory."""
        from octopoid.tasks import cancel_task

        agents_dir, tasks_dir = runtime_dirs
        task_dir = tasks_dir / TASK_ID
        assert task_dir.exists()

        mock_sdk = MagicMock()

        with patch("octopoid.tasks.get_sdk", return_value=mock_sdk), \
             patch("octopoid.git_utils._remove_worktree", return_value=None), \
             patch("octopoid.config.find_parent_project", return_value=Path("/fake/repo")):

            result = cancel_task(TASK_ID)

        assert result["runtime_removed"] is True
        assert not task_dir.exists()

    def test_no_runtime_dir_reports_success(self, runtime_dirs):
        """If no runtime dir exists, runtime_removed is True (nothing to remove)."""
        from octopoid.tasks import cancel_task

        mock_sdk = MagicMock()

        with patch("octopoid.tasks.get_sdk", return_value=mock_sdk):
            result = cancel_task(TASK_ID)

        assert result["runtime_removed"] is True

    def test_worktree_git_failure_falls_back_to_rmtree(self, runtime_dirs, task_dir_with_worktree):
        """If git worktree remove fails, falls back to shutil.rmtree."""
        from octopoid.tasks import cancel_task

        agents_dir, tasks_dir = runtime_dirs
        mock_sdk = MagicMock()

        with patch("octopoid.tasks.get_sdk", return_value=mock_sdk), \
             patch("octopoid.git_utils._remove_worktree", side_effect=Exception("git error")), \
             patch("octopoid.config.find_parent_project", return_value=Path("/fake/repo")):

            result = cancel_task(TASK_ID)

        # The error from git is recorded but rmtree fallback succeeded
        assert result["worktree_removed"] is True
        git_errors = [e for e in result["errors"] if "worktree_remove" in e]
        assert git_errors  # git error was recorded


# ---------------------------------------------------------------------------
# Test: cancel_task deletes server record
# ---------------------------------------------------------------------------


class TestCancelTaskDeletesServer:
    def test_deletes_task_on_server(self, runtime_dirs):
        """cancel_task calls sdk.tasks.delete with the task ID."""
        from octopoid.tasks import cancel_task

        mock_sdk = MagicMock()
        mock_sdk.tasks.delete = MagicMock(return_value=None)

        with patch("octopoid.tasks.get_sdk", return_value=mock_sdk):
            result = cancel_task(TASK_ID)

        mock_sdk.tasks.delete.assert_called_once_with(TASK_ID)
        assert result["server_deleted"] is True

    def test_404_treated_as_success(self, runtime_dirs):
        """A 404 response from the server is treated as success (already deleted)."""
        from octopoid.tasks import cancel_task

        mock_sdk = MagicMock()
        mock_sdk.tasks.delete = MagicMock(side_effect=Exception("404 not found"))

        with patch("octopoid.tasks.get_sdk", return_value=mock_sdk):
            result = cancel_task(TASK_ID)

        assert result["server_deleted"] is True
        server_errors = [e for e in result["errors"] if "server_delete" in e]
        assert not server_errors

    def test_server_error_recorded(self, runtime_dirs):
        """A non-404 server error is recorded in errors."""
        from octopoid.tasks import cancel_task

        mock_sdk = MagicMock()
        mock_sdk.tasks.delete = MagicMock(side_effect=Exception("500 Internal Server Error"))

        with patch("octopoid.tasks.get_sdk", return_value=mock_sdk):
            result = cancel_task(TASK_ID)

        assert result["server_deleted"] is False
        server_errors = [e for e in result["errors"] if "server_delete" in e]
        assert server_errors


# ---------------------------------------------------------------------------
# Test: cancel_task result structure
# ---------------------------------------------------------------------------


class TestCancelTaskResult:
    def test_result_has_expected_keys(self, runtime_dirs):
        """cancel_task always returns a dict with all expected keys."""
        from octopoid.tasks import cancel_task

        mock_sdk = MagicMock()
        mock_sdk.tasks.delete = MagicMock(return_value=None)

        with patch("octopoid.tasks.get_sdk", return_value=mock_sdk):
            result = cancel_task(TASK_ID)

        assert "task_id" in result
        assert "killed_pid" in result
        assert "worktree_removed" in result
        assert "runtime_removed" in result
        assert "server_deleted" in result
        assert "errors" in result
        assert result["task_id"] == TASK_ID

    def test_full_cancel_no_errors(self, runtime_dirs, task_dir_with_worktree, running_agent):
        """Full cancel with all state present completes without errors."""
        from octopoid.tasks import cancel_task

        mock_sdk = MagicMock()
        mock_sdk.tasks.delete = MagicMock(return_value=None)

        with patch("octopoid.tasks.get_sdk", return_value=mock_sdk), \
             patch("os.killpg", return_value=None), \
             patch("os.getpgid", return_value=ALIVE_PID + 1000), \
             patch("octopoid.git_utils._remove_worktree", return_value=None), \
             patch("octopoid.config.find_parent_project", return_value=Path("/fake/repo")):

            result = cancel_task(TASK_ID)

        assert result["killed_pid"] == ALIVE_PID
        assert result["worktree_removed"] is True
        assert result["runtime_removed"] is True
        assert result["server_deleted"] is True
        assert result["errors"] == []
