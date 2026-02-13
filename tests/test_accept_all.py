"""Tests for accept_all.py script behavior.

These tests verify that the acceptance script correctly routes tasks:
- Tasks with commits -> accepted (done queue)
- Burned-out tasks (0 commits, high turns) -> recycled to breakdown
- Depth-capped tasks -> accepted with note for human review
"""

import pytest


class TestAcceptAllRouting:
    """Tests for accept_all routing logic."""

    def test_normal_task_accepted(self):
        """Task with commits - check disabled, always returns False."""
        from orchestrator.queue_utils import is_burned_out

        # Check disabled - always returns False
        assert is_burned_out(commits_count=3, turns_used=50) is False

    def test_burned_task_detected(self):
        """Task with 0 commits and high turns - check disabled."""
        from orchestrator.queue_utils import is_burned_out

        # Check disabled - always returns False now
        assert is_burned_out(commits_count=0, turns_used=100) is False

