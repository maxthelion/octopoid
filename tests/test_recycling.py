"""Tests for task recycling to re-breakdown."""

import pytest


# =============================================================================
# Detection heuristic tests
# =============================================================================


class TestBurnedOutDetection:
    """Tests for the burned-out task detection heuristic.

    NOTE: The is_burned_out() check is currently DISABLED due to false positives
    from commit counting bugs. All tests now verify that the function returns
    False unconditionally. This can be reverted after ephemeral worktrees are
    implemented (TASK-f7b4d710).
    """

    def test_detect_burned_task(self):
        """Task with 0 commits + 100 turns - check disabled, returns False."""
        from orchestrator.queue_utils import is_burned_out

        # Check disabled - always returns False now
        assert is_burned_out(commits_count=0, turns_used=100) is False

    def test_detect_burned_task_at_threshold(self):
        """Task with 0 commits + 80 turns - check disabled, returns False."""
        from orchestrator.queue_utils import is_burned_out

        # Check disabled - always returns False now
        assert is_burned_out(commits_count=0, turns_used=80) is False

    def test_detect_burned_task_low_turns_not_burned(self):
        """Task with 0 commits + 10 turns - check disabled, returns False."""
        from orchestrator.queue_utils import is_burned_out

        # Check disabled - always returns False now
        assert is_burned_out(commits_count=0, turns_used=10) is False

    def test_detect_burned_task_below_threshold_not_burned(self):
        """Task with 0 commits + 50 turns - check disabled, returns False."""
        from orchestrator.queue_utils import is_burned_out

        # Check disabled - always returns False now
        assert is_burned_out(commits_count=0, turns_used=50) is False

    def test_detect_normal_task_with_commits(self):
        """Task with 3 commits + 100 turns - check disabled, returns False."""
        from orchestrator.queue_utils import is_burned_out

        # Check disabled - always returns False now
        assert is_burned_out(commits_count=3, turns_used=100) is False

    def test_orchestrator_impl_zero_commits_high_turns_is_burned(self):
        """orchestrator_impl task with 0 commits + 100 turns - check disabled.

        The check is disabled due to commit counting bugs that affect all roles
        including orchestrator_impl. Even though the role counts submodule commits,
        the persistent worktree + branch switching issue causes false positives.
        """
        from orchestrator.queue_utils import is_burned_out

        # Check disabled - always returns False now
        assert is_burned_out(commits_count=0, turns_used=100) is False

    def test_orchestrator_impl_with_submodule_commits_not_burned(self):
        """orchestrator_impl task with submodule commits - check disabled.

        When the orchestrator_impl role correctly counts submodule commits,
        a task with commits should never be considered burned out.
        """
        from orchestrator.queue_utils import is_burned_out

        # Check disabled - always returns False now
        assert is_burned_out(commits_count=3, turns_used=100) is False
