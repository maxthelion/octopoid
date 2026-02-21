"""Tests for the packages/dashboard Textual dashboard.

Tests cover:
1. Package importability and structure
2. DataManager.fetch_sync() data layer
3. _format_age() utility in the agents tab
4. App class attributes (bindings, title)
5. Tab widget update_data() interface
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Package import tests
# ---------------------------------------------------------------------------


class TestPackageImports:
    """Verify the dashboard package and its modules are importable."""

    def test_main_package_importable(self):
        import packages.dashboard as pkg
        assert pkg is not None

    def test_app_importable(self):
        from packages.dashboard.app import OctopoidDashboard
        assert OctopoidDashboard is not None

    def test_data_manager_importable(self):
        from packages.dashboard.data import DataManager
        assert DataManager is not None

    def test_tabs_importable(self):
        from packages.dashboard.tabs.work import WorkTab, TaskSelected
        from packages.dashboard.tabs.prs import PRsTab
        from packages.dashboard.tabs.inbox import InboxTab
        from packages.dashboard.tabs.agents import AgentsTab
        from packages.dashboard.tabs.done import DoneTab
        from packages.dashboard.tabs.drafts import DraftsTab
        assert all([WorkTab, TaskSelected, PRsTab, InboxTab, AgentsTab, DoneTab, DraftsTab])

    def test_widgets_importable(self):
        from packages.dashboard.widgets.status_badge import StatusBadge
        from packages.dashboard.widgets.task_card import TaskCard
        from packages.dashboard.widgets.task_detail import TaskDetail, TaskDetailModal
        assert all([StatusBadge, TaskCard, TaskDetail, TaskDetailModal])


# ---------------------------------------------------------------------------
# App class attribute tests
# ---------------------------------------------------------------------------


class TestOctopoidDashboard:
    """Tests for OctopoidDashboard app class attributes."""

    def test_title(self):
        from packages.dashboard.app import OctopoidDashboard
        assert OctopoidDashboard.TITLE == "Octopoid"

    def test_has_quit_binding(self):
        from packages.dashboard.app import OctopoidDashboard
        keys = [b.key for b in OctopoidDashboard.BINDINGS]
        assert "q" in keys

    def test_has_refresh_binding(self):
        from packages.dashboard.app import OctopoidDashboard
        keys = [b.key for b in OctopoidDashboard.BINDINGS]
        assert "r" in keys

    def test_has_tab_bindings(self):
        from packages.dashboard.app import OctopoidDashboard
        keys = [b.key for b in OctopoidDashboard.BINDINGS]
        for key in ["w", "i", "a", "t", "f"]:
            assert key in keys, f"Missing binding for key '{key}'"

    def test_css_path_exists(self):
        from packages.dashboard.app import OctopoidDashboard
        assert OctopoidDashboard.CSS_PATH.exists(), (
            f"CSS file not found at {OctopoidDashboard.CSS_PATH}"
        )


# ---------------------------------------------------------------------------
# DataManager tests
# ---------------------------------------------------------------------------


class TestDataManager:
    """Tests for DataManager.fetch_sync()."""

    def test_calls_get_project_report(self):
        from packages.dashboard.data import DataManager

        mock_sdk = MagicMock()
        mock_report = {
            "work": {"incoming": [], "in_progress": [], "in_review": []},
            "prs": [],
            "proposals": [],
            "messages": [],
            "agents": [],
            "health": {},
            "generated_at": datetime.now().isoformat(),
        }

        # Imports are inside fetch_sync(), so patch the source modules
        with patch("orchestrator.sdk.get_sdk", return_value=mock_sdk), \
             patch("orchestrator.reports.get_project_report", return_value=mock_report) as mock_gpr:
            dm = DataManager()
            result = dm.fetch_sync()

        mock_gpr.assert_called_once_with(mock_sdk)
        assert result is mock_report

    def test_propagates_exceptions(self):
        from packages.dashboard.data import DataManager
        import pytest

        with patch("orchestrator.sdk.get_sdk", side_effect=RuntimeError("no config")):
            dm = DataManager()
            with pytest.raises(RuntimeError, match="no config"):
                dm.fetch_sync()


# ---------------------------------------------------------------------------
# format_age utility tests (shared utils module)
# ---------------------------------------------------------------------------


class TestFormatAge:
    """Tests for format_age() in packages/dashboard/utils.py."""

    def _fmt(self, ts: str | None) -> str:
        from packages.dashboard.utils import format_age
        return format_age(ts)

    def test_none_returns_empty(self):
        assert self._fmt(None) == ""

    def test_empty_string_returns_empty(self):
        assert self._fmt("") == ""

    def test_invalid_string_returns_empty(self):
        assert self._fmt("not-a-date") == ""

    def test_recent_shows_seconds(self):
        ts = (datetime.now() - timedelta(seconds=30)).isoformat()
        result = self._fmt(ts)
        assert result.endswith("s")

    def test_minutes(self):
        ts = (datetime.now() - timedelta(minutes=5)).isoformat()
        assert self._fmt(ts) == "5m"

    def test_hours(self):
        ts = (datetime.now() - timedelta(hours=3)).isoformat()
        assert self._fmt(ts) == "3h"

    def test_days(self):
        ts = (datetime.now() - timedelta(days=2)).isoformat()
        assert self._fmt(ts) == "2d"

    def test_future_returns_now(self):
        ts = (datetime.now() + timedelta(hours=1)).isoformat()
        assert self._fmt(ts) == "now"

    def test_handles_z_suffix(self):
        ts = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert self._fmt(ts) == "1h"


# ---------------------------------------------------------------------------
# Tab widget update_data interface tests
# ---------------------------------------------------------------------------


class TestTabUpdateData:
    """Tests that tab widgets expose update_data() and accept a report dict."""

    def _make_report(self) -> dict:
        return {
            "work": {
                "incoming": [{"id": "t1", "title": "Task 1", "role": "implement"}],
                "in_progress": [],
                "checking": [],
                "in_review": [],
            },
            "done_tasks": [],
            "prs": [],
            "proposals": [],
            "messages": [],
            "agents": [],
            "health": {"scheduler": "running", "idle_agents": 0, "running_agents": 0,
                       "total_agents": 0, "queue_depth": 0},
            "generated_at": datetime.now().isoformat(),
        }

    def test_work_tab_has_update_data(self):
        from packages.dashboard.tabs.work import WorkTab
        assert callable(getattr(WorkTab, "update_data", None))

    def test_prs_tab_has_update_data(self):
        from packages.dashboard.tabs.prs import PRsTab
        assert callable(getattr(PRsTab, "update_data", None))

    def test_inbox_tab_has_update_data(self):
        from packages.dashboard.tabs.inbox import InboxTab
        assert callable(getattr(InboxTab, "update_data", None))

    def test_agents_tab_has_update_data(self):
        from packages.dashboard.tabs.agents import AgentsTab
        assert callable(getattr(AgentsTab, "update_data", None))

    def test_done_tab_has_update_data(self):
        from packages.dashboard.tabs.done import DoneTab
        assert callable(getattr(DoneTab, "update_data", None))

    def test_drafts_tab_has_update_data(self):
        from packages.dashboard.tabs.drafts import DraftsTab
        assert callable(getattr(DraftsTab, "update_data", None))


# ---------------------------------------------------------------------------
# TaskSelected message tests
# ---------------------------------------------------------------------------


class TestTaskSelected:
    """Tests for the TaskSelected message class."""

    def test_carries_task_data(self):
        from packages.dashboard.tabs.work import TaskSelected
        task = {"id": "t1", "title": "Fix bug", "role": "implement"}
        msg = TaskSelected(task)
        assert msg.task == task

    def test_task_attribute_is_same_object(self):
        from packages.dashboard.tabs.work import TaskSelected
        task = {"id": "t2", "title": "Add feature"}
        msg = TaskSelected(task)
        assert msg.task is task


# ---------------------------------------------------------------------------
# Wrapper script test
# ---------------------------------------------------------------------------


class TestWrapperScript:
    """Verify the octopoid-dash shell wrapper exists and is executable."""

    def test_wrapper_exists(self):
        from pathlib import Path
        wrapper = Path(__file__).parent.parent / "octopoid-dash"
        assert wrapper.exists(), "octopoid-dash wrapper script not found"

    def test_wrapper_is_executable(self):
        from pathlib import Path
        import os
        wrapper = Path(__file__).parent.parent / "octopoid-dash"
        assert os.access(wrapper, os.X_OK), "octopoid-dash wrapper is not executable"

    def test_wrapper_references_package(self):
        from pathlib import Path
        wrapper = Path(__file__).parent.parent / "octopoid-dash"
        content = wrapper.read_text()
        assert "packages.dashboard" in content
