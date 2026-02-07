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
        assert dash._card_height(task) == 1

    def test_card_with_agent(self):
        task = {"title": "Test task", "agent": "impl-1"}
        assert dash._card_height(task) == 2

    def test_card_with_pr(self):
        task = {"title": "Test task", "pr_number": 50}
        assert dash._card_height(task) == 2

    def test_card_with_agent_and_pr(self):
        task = {"title": "Test task", "agent": "impl-1", "pr_number": 50}
        assert dash._card_height(task) == 3


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

    def test_tab_names_match_count(self):
        assert len(dash.TAB_NAMES) == 4
        assert len(dash.TAB_KEYS) == 4

    def test_tab_keys(self):
        assert dash.TAB_KEYS == ["W", "P", "I", "A"]

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
