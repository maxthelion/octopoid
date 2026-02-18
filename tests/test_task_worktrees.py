"""Tests for ephemeral task-scoped worktrees."""

from pathlib import Path
from unittest.mock import patch, MagicMock

# Note: These imports will work when run from the orchestrator/ directory
# with the proper PYTHONPATH setup
try:
    from orchestrator.git_utils import (
        cleanup_task_worktree,
        create_task_worktree,
        get_task_worktree_path,
        get_task_branch,
    )
except ImportError:
    # Fallback for different import contexts
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from orchestrator.git_utils import (
        cleanup_task_worktree,
        create_task_worktree,
        get_task_worktree_path,
        get_task_branch,
    )


class TestTaskWorktreePath:
    """Tests for get_task_worktree_path()."""

    def test_returns_path_under_tasks_dir(self):
        """Task worktree path should be .octopoid/runtime/tasks/<id>/worktree/."""
        path = get_task_worktree_path("abc12345")
        assert path.name == "worktree"
        assert path.parent.name == "abc12345"
        assert "tasks" in path.parts


class TestGetTaskBranch:
    """Tests for get_task_branch() branch selection logic."""

    def test_breakdown_task_uses_breakdown_branch(self):
        """Breakdown tasks should use breakdown/<breakdown_id> branch."""
        task = {
            "id": "abc12345",
            "breakdown_id": "breakdown-xyz",
            "role": "implement",
        }
        branch = get_task_branch(task)
        assert branch == "breakdown/breakdown-xyz"

    def test_orchestrator_impl_uses_orch_branch(self):
        """Orchestrator_impl tasks should use orch/<id> branch."""
        task = {
            "id": "abc12345",
            "role": "orchestrator_impl",
        }
        branch = get_task_branch(task)
        assert branch == "orch/abc12345"

    def test_regular_task_uses_agent_branch(self):
        """Regular tasks should use agent/<id> branch (no timestamp)."""
        task = {
            "id": "abc12345",
            "role": "implement",
        }
        branch = get_task_branch(task)
        assert branch == "agent/abc12345"


class TestPrepareTaskDirectoryCleansStaleFiles:
    """Tests for stale file cleanup in prepare_task_directory()."""

    def test_cleans_stale_result_json(self, tmp_path, monkeypatch):
        """Stale result.json from a previous run is deleted."""
        monkeypatch.setattr('orchestrator.scheduler.get_tasks_dir', lambda: tmp_path)

        # Create fake agent directory with scripts and prompt
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "scripts").mkdir()
        (agent_dir / "prompt.md").write_text("# Test prompt")

        task_id = "test-task-123"
        task_dir = tmp_path / task_id
        task_dir.mkdir()
        (task_dir / "result.json").write_text('{"outcome": "submitted"}')

        from unittest.mock import patch
        from orchestrator.scheduler import prepare_task_directory

        with patch('orchestrator.git_utils.create_task_worktree', return_value=tmp_path / "worktree"):
            prepare_task_directory(
                {"id": task_id, "role": "implement", "title": "test"},
                "implementer-1",
                {"role": "implementer", "agent_dir": str(agent_dir)},
            )

        assert not (task_dir / "result.json").exists()

    def test_cleans_stale_notes_md(self, tmp_path, monkeypatch):
        """Stale notes.md from a previous run is deleted."""
        monkeypatch.setattr('orchestrator.scheduler.get_tasks_dir', lambda: tmp_path)

        # Create fake agent directory with scripts and prompt
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "scripts").mkdir()
        (agent_dir / "prompt.md").write_text("# Test prompt")

        task_id = "test-task-456"
        task_dir = tmp_path / task_id
        task_dir.mkdir()
        (task_dir / "notes.md").write_text("# Old notes")

        from unittest.mock import patch
        from orchestrator.scheduler import prepare_task_directory

        with patch('orchestrator.git_utils.create_task_worktree', return_value=tmp_path / "worktree"):
            prepare_task_directory(
                {"id": task_id, "role": "implement", "title": "test"},
                "implementer-1",
                {"role": "implementer", "agent_dir": str(agent_dir)},
            )

        assert not (task_dir / "notes.md").exists()


class TestCreateTaskWorktree:
    """Tests for create_task_worktree() base branch selection."""

    def _make_mock_run_git(self, *, verify_rc=0):
        """Build a side_effect for run_git that handles branching logic.

        Args:
            verify_rc: returncode for rev-parse --verify of start_point
        """
        calls = []

        def mock_run_git(args, cwd=None, check=True):
            calls.append(args)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""

            if args[:2] == ["rev-parse", "--verify"]:
                result.returncode = verify_rc

            return result

        return mock_run_git, calls

    def _run(self, task, temp_dir, **kwargs):
        """Run create_task_worktree with standard mocks."""
        worktree_path = temp_dir / "worktree"
        mock_run_git, calls = self._make_mock_run_git(**kwargs)

        with patch('orchestrator.git_utils.find_parent_project', return_value=temp_dir), \
             patch('orchestrator.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('orchestrator.git_utils.get_base_branch', return_value="main"), \
             patch('orchestrator.git_utils.run_git', side_effect=mock_run_git):
            result = create_task_worktree(task)

        return result, calls, worktree_path

    def _find_worktree_add(self, calls):
        """Find the 'git worktree add' call and return its args."""
        add_calls = [c for c in calls if c[:2] == ["worktree", "add"]]
        assert len(add_calls) == 1, f"Expected 1 worktree add call, got {len(add_calls)}: {add_calls}"
        return add_calls[0]

    def test_task_with_custom_branch_uses_origin_branch(self, temp_dir):
        """Task with branch field creates worktree from origin/<branch>."""
        task = {"id": "TASK-abc123", "role": "implement", "branch": "feature/xyz"}

        result, calls, worktree_path = self._run(task, temp_dir)

        add_args = self._find_worktree_add(calls)
        assert add_args[-1] == "origin/feature/xyz"
        assert result == worktree_path

    def test_task_without_branch_uses_main(self, temp_dir):
        """Task without branch field falls back to origin/main."""
        task = {"id": "TASK-abc123", "role": "implement"}

        _, calls, _ = self._run(task, temp_dir)

        add_args = self._find_worktree_add(calls)
        assert add_args[-1] == "origin/main"

    def test_task_branch_fallback_when_origin_missing(self, temp_dir):
        """Falls back to origin/main when task branch doesn't exist on origin."""
        task = {"id": "TASK-abc123", "role": "implement", "branch": "feature/gone"}

        _, calls, _ = self._run(task, temp_dir, verify_rc=1)

        add_args = self._find_worktree_add(calls)
        # Should fall back to origin/main, not use origin/feature/gone
        assert add_args[-1] == "origin/main"

    def test_existing_worktree_is_reused(self, temp_dir):
        """Existing worktree with .git is returned when branch matches."""
        task = {"id": "TASK-abc123", "role": "implement", "branch": "feature/xyz"}
        worktree_path = temp_dir / "worktree"
        worktree_path.mkdir(parents=True)
        (worktree_path / ".git").write_text("gitdir: ...")

        def mock_run_git(args, cwd=None, check=True):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "abc123\n"
            return result

        with patch('orchestrator.git_utils.find_parent_project', return_value=temp_dir), \
             patch('orchestrator.git_utils.get_task_worktree_path', return_value=worktree_path), \
             patch('orchestrator.git_utils.run_git', side_effect=mock_run_git) as mock_run:
            result = create_task_worktree(task)

        assert result == worktree_path
        # Branch check should have been performed but no worktree add
        add_calls = [c for c in mock_run.call_args_list if c[0][0][:2] == ["worktree", "add"]]
        assert len(add_calls) == 0


class TestCleanupTaskWorktree:
    """Tests for cleanup_task_worktree()."""

    def test_handles_nonexistent_worktree_gracefully(self):
        """Should not error if worktree doesn't exist."""
        cleanup_task_worktree("nonexistent-task", push_commits=False)
        # Should not raise
