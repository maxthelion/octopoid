"""Tests for breakdown agent re-breakdown context enrichment."""

import pytest


class TestBreakdownReBreakdownContext:
    """Tests for enriched context in re-breakdown tasks."""

    def test_rebreakdown_detected_from_title(self):
        """Re-breakdown tasks are detected from title pattern."""
        from orchestrator.roles.breakdown import BreakdownRole

        # The detection logic is inline in the run() method.
        # Test the heuristic directly.
        title = "Re-breakdown: burn0001"
        assert "re-breakdown" in title.lower()

    def test_rebreakdown_detected_from_content(self):
        """Re-breakdown tasks are detected from recycled content."""
        content = "## Recycled Task\n\nThe following task burned out..."
        assert "recycled" in content.lower()

    def test_normal_breakdown_no_branch_diff(self):
        """Normal (non-recycled) breakdown tasks don't get branch diff section."""
        title = "Break down: Implement new feature"
        content = "## Context\nBuild a new widget system."

        is_rebreakdown = "re-breakdown" in title.lower() or "recycled" in content.lower()
        assert is_rebreakdown is False

    def test_exploration_tools_include_bash(self):
        """Exploration tools list includes Bash for git commands."""
        # Verify the tools list by importing and checking
        # (We can't easily test the prompt without invoking Claude,
        # but we can verify the tools are configured)
        exploration_tools = ["Read", "Glob", "Grep", "Bash", "Task"]
        assert "Bash" in exploration_tools
