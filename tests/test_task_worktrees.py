"""Tests for ephemeral task-scoped worktrees."""

from pathlib import Path

# Note: These imports will work when run from the orchestrator/ directory
# with the proper PYTHONPATH setup
try:
    from orchestrator.git_utils import (
        cleanup_task_worktree,
        get_task_worktree_path,
        get_task_branch,
    )
except ImportError:
    # Fallback for different import contexts
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from orchestrator.git_utils import (
        cleanup_task_worktree,
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


class TestCleanupTaskWorktree:
    """Tests for cleanup_task_worktree()."""

    def test_handles_nonexistent_worktree_gracefully(self):
        """Should not error if worktree doesn't exist."""
        cleanup_task_worktree("nonexistent-task", push_commits=False)
        # Should not raise
