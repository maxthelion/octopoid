"""Tests for the octopoid-dash.py tabbed project dashboard.

Tests cover: data loading, tab rendering logic, state management,
keybindings, demo mode, and helper functions.

Since curses rendering cannot be easily tested end-to-end, we test:
1. Data layer functions (load_report, load_drafts, demo data generation)
2. DashboardState transitions (tab switching, cursor movement)
3. Helper functions (format_age, card_height)
4. Dashboard.handle_input logic
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the dashboard module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import importlib
dash = importlib.import_module("octopoid-dash")


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestFormatAge:
    """Tests for format_age()."""

    def test_none_returns_empty(self):
        assert dash.format_age(None) == ""

    def test_empty_returns_empty(self):
        assert dash.format_age("") == ""

    def test_invalid_returns_empty(self):
        assert dash.format_age("not-a-date") == ""

    def test_recent_shows_seconds(self):
        now = datetime.now()
        ts = (now - timedelta(seconds=30)).isoformat()
        result = dash.format_age(ts)
        assert result.endswith("s")

    def test_minutes(self):
        ts = (datetime.now() - timedelta(minutes=5)).isoformat()
        result = dash.format_age(ts)
        assert result == "5m"

    def test_hours(self):
        ts = (datetime.now() - timedelta(hours=3)).isoformat()
        result = dash.format_age(ts)
        assert result == "3h"

    def test_days(self):
        ts = (datetime.now() - timedelta(days=2)).isoformat()
        result = dash.format_age(ts)
        assert result == "2d"

    def test_future_returns_now(self):
        ts = (datetime.now() + timedelta(hours=1)).isoformat()
        result = dash.format_age(ts)
        assert result == "now"

    def test_handles_z_suffix(self):
        ts = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = dash.format_age(ts)
        assert result == "1h"


class TestCardHeight:
    """Tests for _card_height()."""

    def test_minimal_card(self):
        task = {"title": "Test task"}
        assert dash._card_height(task) == 2  # id + title

    def test_card_with_agent_no_show(self):
        task = {"title": "Test task", "agent": "impl-1"}
        assert dash._card_height(task) == 2  # agent not shown without show_agent

    def test_card_with_agent_show(self):
        task = {"title": "Test task", "agent": "impl-1"}
        assert dash._card_height(task, show_agent=True) == 3  # id + title + agent

    def test_card_with_agent_and_pr(self):
        task = {"title": "Test task", "agent": "impl-1", "pr_number": 50}
        assert dash._card_height(task, show_agent=True) == 3  # id + title + agent


# ---------------------------------------------------------------------------
# Demo data generation tests
# ---------------------------------------------------------------------------


class TestDemoReport:
    """Tests for _generate_demo_report()."""

    def test_has_all_required_keys(self):
        report = dash._generate_demo_report()
        assert "work" in report
        assert "prs" in report
        assert "proposals" in report
        assert "messages" in report
        assert "agents" in report
        assert "health" in report
        assert "generated_at" in report

    def test_work_has_all_categories(self):
        report = dash._generate_demo_report()
        work = report["work"]
        assert "incoming" in work
        assert "in_progress" in work
        assert "in_review" in work
        assert "done_today" in work

    def test_work_has_tasks(self):
        report = dash._generate_demo_report()
        work = report["work"]
        assert len(work["incoming"]) > 0
        assert len(work["in_progress"]) > 0
        assert len(work["in_review"]) > 0
        assert len(work["done_today"]) > 0

    def test_task_cards_have_required_fields(self):
        report = dash._generate_demo_report()
        for category in report["work"].values():
            for task in category:
                assert "id" in task
                assert "title" in task
                assert "role" in task

    def test_prs_have_required_fields(self):
        report = dash._generate_demo_report()
        for pr in report["prs"]:
            assert "number" in pr
            assert "title" in pr
            assert "branch" in pr

    def test_agents_have_required_fields(self):
        report = dash._generate_demo_report()
        for agent in report["agents"]:
            assert "name" in agent
            assert "role" in agent
            assert "status" in agent
            assert "paused" in agent

    def test_health_has_required_fields(self):
        report = dash._generate_demo_report()
        health = report["health"]
        assert "scheduler" in health
        assert "idle_agents" in health
        assert "running_agents" in health
        assert "total_agents" in health
        assert "queue_depth" in health

    def test_generated_at_is_valid_iso(self):
        report = dash._generate_demo_report()
        dt = datetime.fromisoformat(report["generated_at"])
        assert isinstance(dt, datetime)

    def test_includes_orchestrator_role_tasks(self):
        report = dash._generate_demo_report()
        all_tasks = []
        for category in report["work"].values():
            all_tasks.extend(category)
        orch_tasks = [t for t in all_tasks if t["role"] == "orchestrator_impl"]
        assert len(orch_tasks) > 0, "Should have orchestrator role tasks"

    def test_agents_include_recent_tasks(self):
        report = dash._generate_demo_report()
        agents_with_history = [a for a in report["agents"] if a["recent_tasks"]]
        assert len(agents_with_history) > 0

    def test_demo_tick_advances(self):
        """Demo report changes state between calls."""
        dash._demo_tick = 0
        r1 = dash._generate_demo_report()
        r2 = dash._generate_demo_report()
        # _demo_tick should advance
        assert dash._demo_tick == 2


class TestDemoDrafts:
    """Tests for _generate_demo_drafts()."""

    def test_returns_list(self):
        drafts = dash._generate_demo_drafts()
        assert isinstance(drafts, list)
        assert len(drafts) > 0

    def test_drafts_have_filename_and_title(self):
        drafts = dash._generate_demo_drafts()
        for d in drafts:
            assert "filename" in d
            assert "title" in d
            assert d["filename"].endswith(".md")


# ---------------------------------------------------------------------------
# Data loading tests
# ---------------------------------------------------------------------------


class TestLoadReport:
    """Tests for load_report()."""

    def test_demo_mode_returns_demo_data(self):
        report = dash.load_report(demo_mode=True)
        assert "work" in report
        assert len(report["agents"]) > 0

    @patch("orchestrator.reports.get_project_report")
    def test_live_mode_calls_reports_module(self, mock_report):
        mock_report.return_value = {
            "work": {"incoming": [], "in_progress": [], "in_review": [], "done_today": []},
            "prs": [], "proposals": [], "messages": [], "agents": [],
            "health": {}, "generated_at": datetime.now().isoformat(),
        }
        report = dash.load_report(demo_mode=False)
        assert "work" in report

    def test_live_mode_error_returns_fallback(self):
        """When reports module fails, should return a valid structure."""
        with patch.dict(sys.modules, {"orchestrator.reports": None}):
            report = dash.load_report(demo_mode=False)
            assert "work" in report
            assert "health" in report


class TestLoadDrafts:
    """Tests for load_drafts()."""

    def test_demo_mode_returns_demo_drafts(self):
        drafts = dash.load_drafts(demo_mode=True)
        assert len(drafts) > 0

    def test_returns_empty_for_nonexistent_dir(self):
        with patch("pathlib.Path.cwd", return_value=Path("/nonexistent")):
            drafts = dash.load_drafts(demo_mode=False)
            assert drafts == []

    def test_reads_from_drafts_directory(self, tmp_path):
        drafts_dir = tmp_path / "project-management" / "drafts"
        drafts_dir.mkdir(parents=True)

        (drafts_dir / "test-feature.md").write_text("# Test Feature Plan\n\nContent here.")
        (drafts_dir / "another-plan.md").write_text("# Another Plan\n\nMore content.")

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            drafts = dash.load_drafts(demo_mode=False)
            assert len(drafts) == 2
            titles = [d["title"] for d in drafts]
            assert "Test Feature Plan" in titles
            assert "Another Plan" in titles


# ---------------------------------------------------------------------------
# Dashboard state tests
# ---------------------------------------------------------------------------


class TestDashboardState:
    """Tests for DashboardState."""

    def test_defaults(self):
        state = dash.DashboardState()
        assert state.active_tab == dash.TAB_WORK
        assert state.agent_cursor == 0
        assert state.pr_cursor == 0
        assert state.demo_mode is False

    def test_custom_init(self):
        state = dash.DashboardState(active_tab=dash.TAB_AGENTS, demo_mode=True)
        assert state.active_tab == dash.TAB_AGENTS
        assert state.demo_mode is True


# ---------------------------------------------------------------------------
# Input handling tests (without curses)
# ---------------------------------------------------------------------------


class TestHandleInput:
    """Tests for Dashboard.handle_input() logic.

    We can't test the full curses rendering, but we can test
    the input handling by mocking the stdscr and curses setup.
    """

    def _make_dashboard(self):
        """Create a Dashboard-like object with mocked curses."""
        state = dash.DashboardState(demo_mode=True)
        state.last_report = dash._generate_demo_report()
        state.last_drafts = dash._generate_demo_drafts()

        # Create a minimal mock that acts like Dashboard
        dashboard = MagicMock()
        dashboard.state = state
        dashboard.running = True
        dashboard.handle_input = dash.Dashboard.handle_input.__get__(dashboard)
        dashboard._move_cursor = dash.Dashboard._move_cursor.__get__(dashboard)
        dashboard.load_data = MagicMock()
        return dashboard

    def test_q_returns_false(self):
        d = self._make_dashboard()
        assert d.handle_input(ord('q')) is False

    def test_Q_returns_false(self):
        d = self._make_dashboard()
        assert d.handle_input(ord('Q')) is False

    def test_r_triggers_refresh(self):
        d = self._make_dashboard()
        result = d.handle_input(ord('r'))
        assert result is True
        d.load_data.assert_called_once()

    def test_w_switches_to_work_tab(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_AGENTS
        d.handle_input(ord('w'))
        assert d.state.active_tab == dash.TAB_WORK

    def test_p_switches_to_prs_tab(self):
        d = self._make_dashboard()
        d.handle_input(ord('p'))
        assert d.state.active_tab == dash.TAB_PRS

    def test_i_switches_to_inbox_tab(self):
        d = self._make_dashboard()
        d.handle_input(ord('i'))
        assert d.state.active_tab == dash.TAB_INBOX

    def test_a_switches_to_agents_tab(self):
        d = self._make_dashboard()
        d.handle_input(ord('a'))
        assert d.state.active_tab == dash.TAB_AGENTS

    def test_1_switches_to_work_tab(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_AGENTS
        d.handle_input(ord('1'))
        assert d.state.active_tab == dash.TAB_WORK

    def test_2_switches_to_prs_tab(self):
        d = self._make_dashboard()
        d.handle_input(ord('2'))
        assert d.state.active_tab == dash.TAB_PRS

    def test_3_switches_to_inbox_tab(self):
        d = self._make_dashboard()
        d.handle_input(ord('3'))
        assert d.state.active_tab == dash.TAB_INBOX

    def test_4_switches_to_agents_tab(self):
        d = self._make_dashboard()
        d.handle_input(ord('4'))
        assert d.state.active_tab == dash.TAB_AGENTS

    def test_j_moves_cursor_down_on_agents_tab(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_AGENTS
        d.state.agent_cursor = 0
        d.handle_input(ord('j'))
        assert d.state.agent_cursor == 1

    def test_k_moves_cursor_up_on_agents_tab(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_AGENTS
        d.state.agent_cursor = 2
        d.handle_input(ord('k'))
        assert d.state.agent_cursor == 1

    def test_cursor_clamps_at_top(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_AGENTS
        d.state.agent_cursor = 0
        d.handle_input(ord('k'))
        assert d.state.agent_cursor == 0

    def test_cursor_clamps_at_bottom(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_AGENTS
        agents = d.state.last_report["agents"]
        d.state.agent_cursor = len(agents) - 1
        d.handle_input(ord('j'))
        assert d.state.agent_cursor == len(agents) - 1

    def test_j_moves_pr_cursor(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_PRS
        d.state.pr_cursor = 0
        d.handle_input(ord('j'))
        assert d.state.pr_cursor == 1

    def test_k_moves_pr_cursor_up(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_PRS
        d.state.pr_cursor = 3
        d.handle_input(ord('k'))
        assert d.state.pr_cursor == 2

    def test_pr_cursor_clamps_at_top(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_PRS
        d.state.pr_cursor = 0
        d.handle_input(ord('k'))
        assert d.state.pr_cursor == 0

    def test_f_switches_to_drafts_tab(self):
        d = self._make_dashboard()
        d.handle_input(ord('f'))
        assert d.state.active_tab == dash.TAB_DRAFTS

    def test_F_switches_to_drafts_tab(self):
        d = self._make_dashboard()
        d.handle_input(ord('F'))
        assert d.state.active_tab == dash.TAB_DRAFTS

    def test_6_switches_to_drafts_tab(self):
        d = self._make_dashboard()
        d.handle_input(ord('6'))
        assert d.state.active_tab == dash.TAB_DRAFTS

    def test_j_moves_drafts_cursor_down(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_DRAFTS
        d.state.drafts_cursor = 0
        d.handle_input(ord('j'))
        assert d.state.drafts_cursor == 1

    def test_k_moves_drafts_cursor_up(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_DRAFTS
        d.state.drafts_cursor = 2
        d.handle_input(ord('k'))
        assert d.state.drafts_cursor == 1

    def test_drafts_cursor_clamps_at_top(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_DRAFTS
        d.state.drafts_cursor = 0
        d.handle_input(ord('k'))
        assert d.state.drafts_cursor == 0

    def test_drafts_cursor_clamps_at_bottom(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_DRAFTS
        drafts = d.state.last_drafts
        d.state.drafts_cursor = len(drafts) - 1
        d.handle_input(ord('j'))
        assert d.state.drafts_cursor == len(drafts) - 1


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for module constants."""

    def test_tab_indices(self):
        assert dash.TAB_WORK == 0
        assert dash.TAB_PRS == 1
        assert dash.TAB_INBOX == 2
        assert dash.TAB_AGENTS == 3
        assert dash.TAB_DONE == 4
        assert dash.TAB_DRAFTS == 5

    def test_tab_names_match_count(self):
        assert len(dash.TAB_NAMES) == 6
        assert len(dash.TAB_KEYS) == 6

    def test_tab_keys(self):
        assert dash.TAB_KEYS == ["W", "P", "I", "A", "D", "F"]

    def test_max_turns(self):
        assert dash.MAX_TURNS > 0


# ---------------------------------------------------------------------------
# Rendering smoke tests (with mocked curses window)
# ---------------------------------------------------------------------------


class TestRenderingSmokeTests:
    """Smoke tests for tab renderers using a mocked curses window.

    These verify that renderers don't crash, not that output is correct.
    curses.color_pair() and curses.A_BOLD etc. require initscr(), so we
    patch them to return plain integers.
    """

    @pytest.fixture(autouse=True)
    def _patch_curses(self):
        """Patch curses functions/constants that require initscr()."""
        import curses as _curses
        with patch.object(_curses, "color_pair", side_effect=lambda n: n):
            # ACS_* constants are only defined after initscr() — set them as ints
            if not hasattr(_curses, "ACS_HLINE") or _curses.ACS_HLINE is None:
                _curses.ACS_HLINE = ord("-")
                _curses.ACS_VLINE = ord("|")
                _curses.ACS_ULCORNER = ord("+")
                _curses.ACS_URCORNER = ord("+")
                _curses.ACS_LLCORNER = ord("+")
                _curses.ACS_LRCORNER = ord("+")
            yield

    def _mock_win(self, rows=40, cols=120):
        """Create a mock curses window."""
        win = MagicMock()
        win.getmaxyx.return_value = (rows, cols)
        win.addnstr = MagicMock()
        win.addstr = MagicMock()
        win.hline = MagicMock()
        win.attron = MagicMock()
        win.attroff = MagicMock()
        return win

    def test_work_tab_renders(self):
        win = self._mock_win()
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        dash.render_work_tab(win, report, state)
        # Should have called addnstr at least once
        assert win.addnstr.called

    def test_prs_tab_renders(self):
        win = self._mock_win()
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        dash.render_prs_tab(win, report, state)
        assert win.addnstr.called

    def test_inbox_tab_renders(self):
        win = self._mock_win()
        report = dash._generate_demo_report()
        drafts = dash._generate_demo_drafts()
        state = dash.DashboardState()
        dash.render_inbox_tab(win, report, drafts, state)
        assert win.addnstr.called

    def test_agents_tab_renders(self):
        win = self._mock_win()
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        dash.render_agents_tab(win, report, state)
        assert win.addnstr.called

    def test_work_tab_empty_report(self):
        win = self._mock_win()
        report = {"work": {"incoming": [], "in_progress": [], "in_review": [], "done_today": []}}
        state = dash.DashboardState()
        dash.render_work_tab(win, report, state)

    def test_prs_tab_empty(self):
        win = self._mock_win()
        report = {"prs": []}
        state = dash.DashboardState()
        dash.render_prs_tab(win, report, state)

    def test_inbox_tab_empty(self):
        win = self._mock_win()
        report = {"proposals": [], "messages": []}
        state = dash.DashboardState()
        dash.render_inbox_tab(win, report, [], state)

    def test_agents_tab_empty(self):
        win = self._mock_win()
        report = {"agents": [], "health": {}}
        state = dash.DashboardState()
        dash.render_agents_tab(win, report, state)

    def test_work_tab_small_terminal(self):
        win = self._mock_win(rows=12, cols=50)
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        dash.render_work_tab(win, report, state)

    def test_agents_tab_cursor_at_last(self):
        win = self._mock_win()
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        state.agent_cursor = len(report["agents"]) - 1
        dash.render_agents_tab(win, report, state)

    def test_drafts_tab_renders_with_drafts(self):
        win = self._mock_win()
        drafts = dash._generate_demo_drafts()
        state = dash.DashboardState()
        state.drafts_content = "# Draft Title\n\nSome content here."
        dash.render_drafts_tab(win, drafts, state)
        assert win.addnstr.called

    def test_drafts_tab_renders_empty(self):
        win = self._mock_win()
        state = dash.DashboardState()
        dash.render_drafts_tab(win, [], state)
        # Should not crash on empty drafts list
        assert win.addnstr.called

    def test_drafts_tab_cursor_selection(self):
        win = self._mock_win()
        drafts = dash._generate_demo_drafts()
        state = dash.DashboardState()
        state.drafts_cursor = len(drafts) - 1
        state.drafts_content = "# Last Draft\n\nContent."
        dash.render_drafts_tab(win, drafts, state)
        assert win.addnstr.called

    def test_drafts_tab_no_content_loaded(self):
        win = self._mock_win()
        drafts = dash._generate_demo_drafts()
        state = dash.DashboardState()
        state.drafts_content = None
        dash.render_drafts_tab(win, drafts, state)
        # Should not crash when no content is cached
        assert win.addnstr.called


# ---------------------------------------------------------------------------
# Safe drawing helper tests
# ---------------------------------------------------------------------------


class TestSafeAddstr:
    """Tests for safe_addstr()."""

    def test_writes_within_bounds(self):
        win = MagicMock()
        win.getmaxyx.return_value = (24, 80)
        dash.safe_addstr(win, 0, 0, "hello")
        win.addnstr.assert_called_once()

    def test_skips_negative_y(self):
        win = MagicMock()
        win.getmaxyx.return_value = (24, 80)
        dash.safe_addstr(win, -1, 0, "hello")
        win.addnstr.assert_not_called()

    def test_skips_out_of_bounds_y(self):
        win = MagicMock()
        win.getmaxyx.return_value = (24, 80)
        dash.safe_addstr(win, 25, 0, "hello")
        win.addnstr.assert_not_called()

    def test_skips_out_of_bounds_x(self):
        win = MagicMock()
        win.getmaxyx.return_value = (24, 80)
        dash.safe_addstr(win, 0, 80, "hello")
        win.addnstr.assert_not_called()

    def test_respects_max_x(self):
        win = MagicMock()
        win.getmaxyx.return_value = (24, 80)
        dash.safe_addstr(win, 0, 0, "hello world", max_x=5)
        # Should clip to 5 chars from position 0
        win.addnstr.assert_called_once()
        args = win.addnstr.call_args
        assert args[0][3] == 5  # limit parameter


class TestSafeHline:
    """Tests for safe_hline()."""

    def test_draws_line(self):
        import curses
        win = MagicMock()
        dash.safe_hline(win, 0, 0, ord("-"), 10)
        win.hline.assert_called_once()

    def test_handles_curses_error(self):
        import curses
        win = MagicMock()
        win.hline.side_effect = curses.error("test")
        # Should not raise
        dash.safe_hline(win, 0, 0, ord("-"), 10)


# ---------------------------------------------------------------------------
# _flatten_work_tasks tests
# ---------------------------------------------------------------------------


class TestFlattenWorkTasks:
    """Tests for _flatten_work_tasks()."""

    def test_empty_report(self):
        report = {"work": {}}
        assert dash._flatten_work_tasks(report) == []

    def test_orders_columns_left_to_right(self):
        """Tasks from incoming come before in_progress, then checking, then in_review."""
        report = {"work": {
            "incoming": [{"id": "t1"}],
            "in_progress": [{"id": "t2"}],
            "checking": [{"id": "t3"}],
            "in_review": [{"id": "t4"}],
        }}
        flat = dash._flatten_work_tasks(report)
        ids = [t["id"] for t in flat]
        assert ids == ["t1", "t2", "t3", "t4"]

    def test_multiple_per_column(self):
        report = {"work": {
            "incoming": [{"id": "t1"}, {"id": "t2"}],
            "in_progress": [{"id": "t3"}],
        }}
        flat = dash._flatten_work_tasks(report)
        ids = [t["id"] for t in flat]
        assert ids == ["t1", "t2", "t3"]

    def test_demo_data_has_tasks(self):
        report = dash._generate_demo_report()
        flat = dash._flatten_work_tasks(report)
        assert len(flat) > 0

    def test_missing_work_key(self):
        assert dash._flatten_work_tasks({}) == []


# ---------------------------------------------------------------------------
# Work Board cursor navigation tests
# ---------------------------------------------------------------------------


class TestWorkCursorNavigation:
    """Tests for j/k cursor movement on the Work Board tab."""

    def _make_dashboard(self):
        state = dash.DashboardState(demo_mode=True)
        state.last_report = dash._generate_demo_report()
        state.last_drafts = dash._generate_demo_drafts()
        dashboard = MagicMock()
        dashboard.state = state
        dashboard.running = True
        dashboard.handle_input = dash.Dashboard.handle_input.__get__(dashboard)
        dashboard._move_cursor = dash.Dashboard._move_cursor.__get__(dashboard)
        dashboard.load_data = MagicMock()
        return dashboard

    def test_j_moves_work_cursor_down(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_WORK
        d.state.work_cursor = 0
        d.handle_input(ord('j'))
        assert d.state.work_cursor == 1

    def test_k_moves_work_cursor_up(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_WORK
        d.state.work_cursor = 2
        d.handle_input(ord('k'))
        assert d.state.work_cursor == 1

    def test_work_cursor_clamps_at_top(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_WORK
        d.state.work_cursor = 0
        d.handle_input(ord('k'))
        assert d.state.work_cursor == 0

    def test_work_cursor_clamps_at_bottom(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_WORK
        flat = dash._flatten_work_tasks(d.state.last_report)
        d.state.work_cursor = len(flat) - 1
        d.handle_input(ord('j'))
        assert d.state.work_cursor == len(flat) - 1

    def test_cursor_navigates_across_columns(self):
        """Cursor should move through tasks across multiple columns."""
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_WORK
        d.state.work_cursor = 0
        flat = dash._flatten_work_tasks(d.state.last_report)
        # Move to the end
        for _ in range(len(flat)):
            d.handle_input(ord('j'))
        assert d.state.work_cursor == len(flat) - 1

    def test_down_arrow_works(self):
        import curses
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_WORK
        d.state.work_cursor = 0
        d.handle_input(curses.KEY_DOWN)
        assert d.state.work_cursor == 1

    def test_up_arrow_works(self):
        import curses
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_WORK
        d.state.work_cursor = 3
        d.handle_input(curses.KEY_UP)
        assert d.state.work_cursor == 2


# ---------------------------------------------------------------------------
# Work Board detail view tests
# ---------------------------------------------------------------------------


class TestWorkDetailView:
    """Tests for Enter to open and Esc to close task detail view."""

    def _make_dashboard(self):
        state = dash.DashboardState(demo_mode=True)
        state.last_report = dash._generate_demo_report()
        state.last_drafts = dash._generate_demo_drafts()
        dashboard = MagicMock()
        dashboard.state = state
        dashboard.running = True
        dashboard.handle_input = dash.Dashboard.handle_input.__get__(dashboard)
        dashboard._move_cursor = dash.Dashboard._move_cursor.__get__(dashboard)
        dashboard.load_data = MagicMock()
        return dashboard

    def test_enter_opens_detail(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_WORK
        d.state.work_cursor = 0
        flat = dash._flatten_work_tasks(d.state.last_report)
        d.handle_input(ord('\n'))
        assert d.state.work_detail_task_id == flat[0]["id"]

    def test_esc_closes_detail(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_WORK
        d.state.work_detail_task_id = "some-id"
        d.handle_input(27)  # Esc key
        assert d.state.work_detail_task_id is None

    def test_enter_on_non_work_tab_does_not_open_detail(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_AGENTS
        d.handle_input(ord('\n'))
        assert d.state.work_detail_task_id is None

    def test_enter_with_empty_work(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_WORK
        d.state.last_report = {"work": {}}
        d.handle_input(ord('\n'))
        assert d.state.work_detail_task_id is None

    def test_cursor_preserved_after_close(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_WORK
        d.state.work_cursor = 3
        flat = dash._flatten_work_tasks(d.state.last_report)
        d.handle_input(ord('\n'))
        assert d.state.work_detail_task_id == flat[3]["id"]
        d.handle_input(27)  # Esc
        assert d.state.work_detail_task_id is None
        assert d.state.work_cursor == 3  # preserved

    def test_detail_does_not_open_when_already_open(self):
        """Enter should not change the detail task if already viewing one."""
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_WORK
        d.state.work_detail_task_id = "existing-id"
        d.handle_input(ord('\n'))
        # Should not change because detail is already open
        assert d.state.work_detail_task_id == "existing-id"


class TestWorkDetailRendering:
    """Smoke tests for the detail view renderer."""

    @pytest.fixture(autouse=True)
    def _patch_curses(self):
        import curses as _curses
        with patch.object(_curses, "color_pair", side_effect=lambda n: n):
            if not hasattr(_curses, "ACS_HLINE") or _curses.ACS_HLINE is None:
                _curses.ACS_HLINE = ord("-")
            yield

    def _mock_win(self, rows=40, cols=120):
        win = MagicMock()
        win.getmaxyx.return_value = (rows, cols)
        win.addnstr = MagicMock()
        win.addstr = MagicMock()
        win.hline = MagicMock()
        win.attron = MagicMock()
        win.attroff = MagicMock()
        return win

    def test_detail_renders_without_crash(self):
        win = self._mock_win()
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        flat = dash._flatten_work_tasks(report)
        state.work_detail_task_id = flat[0]["id"]
        # Should not crash
        dash.render_work_tab(win, report, state)
        assert win.addnstr.called

    def test_detail_renders_task_with_checks(self):
        """Detail view should handle tasks with checks and check_results."""
        win = self._mock_win()
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        # Find a task with checks (from the checking column)
        checking = report["work"].get("checking", [])
        if checking:
            state.work_detail_task_id = checking[0]["id"]
            dash.render_work_tab(win, report, state)
            assert win.addnstr.called

    def test_detail_renders_task_not_found(self):
        win = self._mock_win()
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        state.work_detail_task_id = "nonexistent"
        # Should not crash
        dash.render_work_tab(win, report, state)

    def test_detail_renders_in_small_terminal(self):
        win = self._mock_win(rows=15, cols=60)
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        flat = dash._flatten_work_tasks(report)
        state.work_detail_task_id = flat[0]["id"]
        dash.render_work_tab(win, report, state)

    def test_work_tab_with_highlight(self):
        """Work tab should render highlighted card without crash."""
        win = self._mock_win()
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        state.work_cursor = 2
        dash.render_work_tab(win, report, state)
        assert win.addnstr.called

    def test_work_tab_highlight_at_last_task(self):
        win = self._mock_win()
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        flat = dash._flatten_work_tasks(report)
        state.work_cursor = len(flat) - 1
        dash.render_work_tab(win, report, state)

    def test_done_tab_renders(self):
        win = self._mock_win()
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        dash.render_done_tab(win, report, state)
        assert win.addnstr.called

    def test_done_tab_empty(self):
        win = self._mock_win()
        report = {"done_tasks": []}
        state = dash.DashboardState()
        dash.render_done_tab(win, report, state)

    def test_done_tab_small_terminal(self):
        win = self._mock_win(rows=12, cols=50)
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        dash.render_done_tab(win, report, state)

    def test_done_tab_with_cursor(self):
        win = self._mock_win()
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        state.done_cursor = 2
        dash.render_done_tab(win, report, state)
        assert win.addnstr.called

    def test_done_tab_cursor_at_last(self):
        win = self._mock_win()
        report = dash._generate_demo_report()
        state = dash.DashboardState()
        done_tasks = report.get("done_tasks", [])
        if done_tasks:
            state.done_cursor = len(done_tasks) - 1
        dash.render_done_tab(win, report, state)

    def test_done_tab_missing_key(self):
        """Should handle report without done_tasks key."""
        win = self._mock_win()
        report = {}
        state = dash.DashboardState()
        dash.render_done_tab(win, report, state)


# ---------------------------------------------------------------------------
# Done tab input handling tests
# ---------------------------------------------------------------------------


class TestDoneTabInput:
    """Tests for Done tab switching and cursor navigation."""

    def _make_dashboard(self):
        state = dash.DashboardState(demo_mode=True)
        state.last_report = dash._generate_demo_report()
        state.last_drafts = dash._generate_demo_drafts()
        dashboard = MagicMock()
        dashboard.state = state
        dashboard.running = True
        dashboard.handle_input = dash.Dashboard.handle_input.__get__(dashboard)
        dashboard._move_cursor = dash.Dashboard._move_cursor.__get__(dashboard)
        dashboard.load_data = MagicMock()
        return dashboard

    def test_d_switches_to_done_tab(self):
        d = self._make_dashboard()
        d.handle_input(ord('d'))
        assert d.state.active_tab == dash.TAB_DONE

    def test_5_switches_to_done_tab(self):
        d = self._make_dashboard()
        d.handle_input(ord('5'))
        assert d.state.active_tab == dash.TAB_DONE

    def test_j_moves_done_cursor_down(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_DONE
        d.state.done_cursor = 0
        d.handle_input(ord('j'))
        assert d.state.done_cursor == 1

    def test_k_moves_done_cursor_up(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_DONE
        d.state.done_cursor = 3
        d.handle_input(ord('k'))
        assert d.state.done_cursor == 2

    def test_done_cursor_clamps_at_top(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_DONE
        d.state.done_cursor = 0
        d.handle_input(ord('k'))
        assert d.state.done_cursor == 0

    def test_done_cursor_clamps_at_bottom(self):
        d = self._make_dashboard()
        d.state.active_tab = dash.TAB_DONE
        done_tasks = d.state.last_report.get("done_tasks", [])
        d.state.done_cursor = len(done_tasks) - 1
        d.handle_input(ord('j'))
        assert d.state.done_cursor == len(done_tasks) - 1


# ---------------------------------------------------------------------------
# Done tab demo data tests
# ---------------------------------------------------------------------------


class TestDoneDemoData:
    """Tests for done_tasks in demo data."""

    def test_demo_report_has_done_tasks(self):
        report = dash._generate_demo_report()
        assert "done_tasks" in report
        assert len(report["done_tasks"]) > 0

    def test_done_tasks_have_required_fields(self):
        report = dash._generate_demo_report()
        for task in report["done_tasks"]:
            assert "id" in task
            assert "title" in task
            assert "final_queue" in task
            assert "completed_at" in task
            assert "accepted_by" in task

    def test_done_tasks_include_different_final_queues(self):
        report = dash._generate_demo_report()
        queues = {t["final_queue"] for t in report["done_tasks"]}
        assert "done" in queues, "Should include done tasks"
        assert "failed" in queues, "Should include failed tasks"
        assert "recycled" in queues, "Should include recycled tasks"

    def test_done_tasks_include_merge_methods(self):
        report = dash._generate_demo_report()
        methods = {t.get("accepted_by") for t in report["done_tasks"]}
        assert "self-merge" in methods
        assert "human" in methods

    def test_done_tasks_include_orchestrator_role(self):
        report = dash._generate_demo_report()
        orch = [t for t in report["done_tasks"] if t.get("role") == "orchestrator_impl"]
        assert len(orch) > 0

    def test_done_tasks_sorted_most_recent_first(self):
        report = dash._generate_demo_report()
        timestamps = [t.get("completed_at", "") for t in report["done_tasks"]]
        assert timestamps == sorted(timestamps, reverse=True)
