"""Tests for pre-check burned-out task recycling."""

import pytest


class TestPreCheckBurnedOutRouting:
    """Tests for pre-check routing burned-out tasks to recycling."""

    def test_pre_check_accepts_task_with_commits(self):
        """Pre-check accepts tasks that have commits even with max turns."""
        from orchestrator.queue_utils import is_burned_out

        # A task with commits is not burned out regardless of turns
        assert is_burned_out(commits_count=2, turns_used=100) is False
        assert is_burned_out(commits_count=1, turns_used=80) is False

    # The following tests were removed during database cleanup:
    # - test_pre_check_recycles_burned_task (used orchestrator.db.get_task)
    # - test_pre_check_cumulative_recycle (used orchestrator.db.get_task)
    # - test_pre_check_depth_cap_accepts (used orchestrator.db.create_task, update_task_queue, get_task)
    # New tests should use the SDK/API instead.
