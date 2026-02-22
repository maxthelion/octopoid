"""Tests for the packages/dashboard Textual dashboard.

Tests cover:
1. Package importability and structure
2. DataManager.fetch_sync() data layer
3. _format_age() utility in the agents tab
4. App class attributes (bindings, title)
5. Tab widget update_data() interface
6. DraftsTab stale file_path fix (drafts created while dashboard is running)
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
# FlowKanban and WorkTab flow-based tests
# ---------------------------------------------------------------------------


class TestFlowKanban:
    """Tests for the FlowKanban widget class."""

    def test_flow_kanban_importable(self):
        from packages.dashboard.tabs.work import FlowKanban
        assert FlowKanban is not None

    def test_flow_kanban_accepts_flow_dict(self):
        from packages.dashboard.tabs.work import FlowKanban
        flow = {"name": "default", "states": ["incoming", "claimed", "provisional"]}
        kanban = FlowKanban(flow=flow, tasks_by_queue={}, agent_map={})
        assert kanban._flow == flow
        assert kanban._tasks_by_queue == {}
        assert kanban._agent_map == {}


class TestWorkTabFlowGrouping:
    """Tests for WorkTab flow-based task grouping logic."""

    def _make_report_with_flows(self) -> dict:
        return {
            "work": {
                "incoming": [
                    {"id": "t1", "title": "Task 1", "queue": "incoming", "flow": "default"},
                ],
                "in_progress": [
                    {"id": "t2", "title": "Task 2", "queue": "claimed", "flow": "default"},
                ],
                "checking": [],
                "in_review": [],
                "done_today": [],
            },
            "flows": [
                {"name": "default", "states": ["incoming", "claimed", "provisional", "done"]},
            ],
            "done_tasks": [],
            "prs": [],
            "proposals": [],
            "messages": [],
            "agents": [],
            "health": {"scheduler": "running", "idle_agents": 0, "running_agents": 0,
                       "total_agents": 0, "queue_depth": 0},
            "generated_at": datetime.now().isoformat(),
        }

    def test_work_tab_importable_with_new_classes(self):
        from packages.dashboard.tabs.work import WorkTab, FlowKanban, WorkColumn
        assert WorkTab is not None
        assert FlowKanban is not None
        assert WorkColumn is not None

    def test_work_tab_has_update_data_method(self):
        from packages.dashboard.tabs.work import WorkTab
        assert callable(getattr(WorkTab, "update_data", None))

    def test_work_tab_accepts_flows_in_report(self):
        from packages.dashboard.tabs.work import WorkTab
        report = self._make_report_with_flows()
        tab = WorkTab(report=report)
        # Verify it stores the report correctly
        assert tab._report.get("flows") == report["flows"]

    def test_task_grouping_by_flow_and_queue(self):
        """Verify the grouping logic used in WorkTab.compose()."""
        tasks = [
            {"id": "t1", "flow": "default", "queue": "incoming"},
            {"id": "t2", "flow": "default", "queue": "claimed"},
            {"id": "t3", "flow": "review", "queue": "incoming"},
        ]

        tasks_by_flow_queue: dict = {}
        for task in tasks:
            flow_name = task.get("flow") or "default"
            queue_name = task.get("queue") or "incoming"
            if flow_name not in tasks_by_flow_queue:
                tasks_by_flow_queue[flow_name] = {}
            if queue_name not in tasks_by_flow_queue[flow_name]:
                tasks_by_flow_queue[flow_name][queue_name] = []
            tasks_by_flow_queue[flow_name][queue_name].append(task)

        assert len(tasks_by_flow_queue["default"]["incoming"]) == 1
        assert tasks_by_flow_queue["default"]["incoming"][0]["id"] == "t1"
        assert len(tasks_by_flow_queue["default"]["claimed"]) == 1
        assert tasks_by_flow_queue["default"]["claimed"][0]["id"] == "t2"
        assert len(tasks_by_flow_queue["review"]["incoming"]) == 1
        assert tasks_by_flow_queue["review"]["incoming"][0]["id"] == "t3"

    def test_tasks_with_missing_flow_default_to_default(self):
        """Tasks without a flow field should be placed in the 'default' flow."""
        tasks = [
            {"id": "t1", "queue": "incoming"},  # no flow field
            {"id": "t2", "flow": None, "queue": "incoming"},  # null flow
        ]

        tasks_by_flow_queue: dict = {}
        for task in tasks:
            flow_name = task.get("flow") or "default"
            queue_name = task.get("queue") or "incoming"
            if flow_name not in tasks_by_flow_queue:
                tasks_by_flow_queue[flow_name] = {}
            if queue_name not in tasks_by_flow_queue[flow_name]:
                tasks_by_flow_queue[flow_name][queue_name] = []
            tasks_by_flow_queue[flow_name][queue_name].append(task)

        assert len(tasks_by_flow_queue.get("default", {}).get("incoming", [])) == 2


# ---------------------------------------------------------------------------
# _order_states_by_transitions tests
# ---------------------------------------------------------------------------


class TestOrderStatesByTransitions:
    """Tests for the _order_states_by_transitions() helper in work.py."""

    def _order(self, states: list, transitions: list) -> list:
        from packages.dashboard.tabs.work import _order_states_by_transitions
        return _order_states_by_transitions(states, transitions)

    def test_lifecycle_order_from_transitions(self):
        """Server returns states alphabetically; transitions define lifecycle order."""
        states = ["claimed", "done", "failed", "incoming", "provisional"]
        transitions = [
            {"from": "incoming", "to": "claimed"},
            {"from": "claimed", "to": "provisional"},
            {"from": "provisional", "to": "done"},
        ]
        result = self._order(states, transitions)
        # Main chain first, in lifecycle order
        assert result[:4] == ["incoming", "claimed", "provisional", "done"]
        # "failed" not in any transition → appended at end
        assert result[4] == "failed"

    def test_isolated_states_appended_at_end(self):
        """States not in any transition are appended after the main chain."""
        states = ["a", "b", "c", "orphan"]
        transitions = [
            {"from": "a", "to": "b"},
            {"from": "b", "to": "c"},
        ]
        result = self._order(states, transitions)
        assert result == ["a", "b", "c", "orphan"]

    def test_empty_transitions_preserves_original_order(self):
        """When no transitions, all states are isolated and returned in input order."""
        states = ["incoming", "claimed", "done"]
        result = self._order(states, [])
        assert result == ["incoming", "claimed", "done"]

    def test_empty_states_returns_empty(self):
        result = self._order([], [{"from": "a", "to": "b"}])
        assert result == []

    def test_single_state_no_transitions(self):
        result = self._order(["incoming"], [])
        assert result == ["incoming"]

    def test_transitions_referencing_unknown_states_ignored(self):
        """Transitions with states not in the states list are ignored."""
        states = ["incoming", "done"]
        transitions = [
            {"from": "incoming", "to": "claimed"},  # "claimed" not in states
            {"from": "claimed", "to": "done"},       # "claimed" not in states
        ]
        # "incoming" appears in "from" of a transition but "claimed" isn't in states
        # so "incoming" is connected (appears as "from"), "done" is not reachable
        result = self._order(states, transitions)
        # Both states remain; "incoming" is connected (appears in transitions as from)
        # but its neighbor "claimed" is not in states, so "incoming" has in_degree=0
        # and "done" is isolated
        assert set(result) == {"incoming", "done"}

    def test_multiple_isolated_states(self):
        """Multiple disconnected states all appear after the main chain."""
        states = ["incoming", "claimed", "failed", "recycled"]
        transitions = [{"from": "incoming", "to": "claimed"}]
        result = self._order(states, transitions)
        assert result[:2] == ["incoming", "claimed"]
        assert set(result[2:]) == {"failed", "recycled"}

    def test_importable_from_work_module(self):
        from packages.dashboard.tabs.work import _order_states_by_transitions
        assert callable(_order_states_by_transitions)


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


# ---------------------------------------------------------------------------
# DraftsTab stale file_path fix
# ---------------------------------------------------------------------------


class TestDraftsTabStalePath:
    """Tests for the fix that re-renders content when file_path is late-set.

    Background: the /draft-idea skill registers a draft on the server with
    file_path=None, then writes the file and PATCHes file_path.  If the
    dashboard fetches between those two steps it caches file_path=None and
    the content pane shows "_empty_" forever — even after file_path is set —
    unless we explicitly re-render on the next update_data() call.
    """

    def _make_tab(self) -> "DraftsTab":
        from packages.dashboard.tabs.drafts import DraftsTab
        tab = DraftsTab()
        # Simulate a selected draft that has no file_path yet
        tab._selected_draft = {"id": 42, "title": "My Draft", "file_path": None, "actions": []}
        tab._selected_draft_id = 42
        return tab

    def _make_report(self, file_path: str | None) -> dict:
        return {
            "drafts": [
                {
                    "id": 42,
                    "title": "My Draft",
                    "status": "idea",
                    "author": "human",
                    "file_path": file_path,
                    "created_at": None,
                    "actions": [],
                }
            ]
        }

    def test_update_data_rerenders_content_when_file_path_becomes_available(self, tmp_path):
        """update_data() should re-render the content pane when file_path is newly set."""
        from packages.dashboard.tabs.drafts import DraftsTab, _load_draft_content

        draft_file = tmp_path / "42-2026-01-01-my-draft.md"
        draft_file.write_text("# My Draft\n\nHello world.")

        tab = self._make_tab()

        # Track calls to query_one("#draft-content") by monkeypatching _load_draft_content
        rendered_content: list[str] = []

        original_load = _load_draft_content

        def fake_load(draft: dict) -> str:
            result = original_load(draft)
            if result:
                rendered_content.append(result)
            return result

        mock_md = MagicMock()

        def fake_query_one(selector: str, widget_type=None):
            if selector == "#draft-content":
                return mock_md
            raise Exception(f"unexpected query: {selector}")

        tab.query_one = fake_query_one

        with patch("packages.dashboard.tabs.drafts._load_draft_content", side_effect=fake_load):
            # First update: file_path is None — no re-render expected
            tab.update_data(self._make_report(file_path=None))
            assert mock_md.update.call_count == 0

            # Second update: file_path is now set — content should be re-rendered
            tab.update_data(self._make_report(file_path=str(draft_file)))
            assert mock_md.update.call_count == 1

    def test_update_data_no_rerender_when_file_path_unchanged(self, tmp_path):
        """update_data() should NOT re-render when file_path was already set."""
        from packages.dashboard.tabs.drafts import DraftsTab

        draft_file = tmp_path / "42-draft.md"
        draft_file.write_text("# Draft")

        tab = self._make_tab()
        # Simulate: file_path was already set in the previous fetch
        tab._selected_draft["file_path"] = str(draft_file)

        mock_md = MagicMock()
        tab.query_one = lambda sel, wt=None: mock_md if sel == "#draft-content" else (_ for _ in ()).throw(Exception(sel))

        # Both before and after have the same file_path — no re-render
        tab.update_data(self._make_report(file_path=str(draft_file)))
        assert mock_md.update.call_count == 0

    def test_update_data_no_rerender_when_file_path_remains_none(self):
        """update_data() should NOT re-render when file_path is still None."""
        from packages.dashboard.tabs.drafts import DraftsTab

        tab = self._make_tab()
        mock_md = MagicMock()
        tab.query_one = lambda sel, wt=None: mock_md if sel == "#draft-content" else (_ for _ in ()).throw(Exception(sel))

        tab.update_data(self._make_report(file_path=None))
        assert mock_md.update.call_count == 0

    def test_load_draft_content_returns_empty_for_none_file_path(self):
        """_load_draft_content returns '' when file_path is None."""
        from packages.dashboard.tabs.drafts import _load_draft_content
        assert _load_draft_content({"file_path": None}) == ""

    def test_load_draft_content_reads_file(self, tmp_path):
        """_load_draft_content returns file contents for a valid file_path."""
        from packages.dashboard.tabs.drafts import _load_draft_content
        f = tmp_path / "draft.md"
        f.write_text("# Hello")
        assert _load_draft_content({"file_path": str(f)}) == "# Hello"

    def test_load_draft_content_returns_error_msg_for_missing_file(self, tmp_path):
        """_load_draft_content returns error string for a non-existent path."""
        from packages.dashboard.tabs.drafts import _load_draft_content
        missing = str(tmp_path / "nonexistent.md")
        assert _load_draft_content({"file_path": missing}) == "(could not read file)"
