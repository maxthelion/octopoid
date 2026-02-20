"""Octopoid Dashboard — Textual TUI app.

Launch with: python -m packages.dashboard
"""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from .data import DataManager
from .tabs.agents import AgentsTab
from .tabs.drafts import DraftsTab
from .tabs.inbox import InboxTab
from .tabs.tasks import TasksTab
from .tabs.work import TaskSelected, WorkTab
from .widgets.task_detail import TaskDetailModal


class OctopoidDashboard(App):
    """Octopoid TUI dashboard built with Textual.

    Five tabs: Work, Inbox, Agents, Tasks, Drafts.
    All tabs are fully implemented. Press Enter on a task to open a detail
    modal; Escape closes it.
    """

    CSS_PATH = Path(__file__).parent / "styles" / "dashboard.tcss"

    TITLE = "Octopoid"
    SUB_TITLE = "Dashboard"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("w", "show_tab('work')", "Work", show=False),
        Binding("i", "show_tab('inbox')", "Inbox", show=False),
        Binding("a", "show_tab('agents')", "Agents", show=False),
        Binding("t", "show_tab('tasks')", "Tasks", show=False),
        Binding("f", "show_tab('drafts')", "Drafts", show=False),
    ]

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._data_manager = DataManager()
        self._report: dict = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs"):
            with TabPane("Work [W]", id="work"):
                yield WorkTab(id="work-tab")
            with TabPane("Inbox [I]", id="inbox"):
                yield InboxTab(id="inbox-tab")
            with TabPane("Agents [A]", id="agents"):
                yield AgentsTab(id="agents-tab")
            with TabPane("Tasks [T]", id="tasks"):
                yield TasksTab(id="tasks-tab")
            with TabPane("Drafts [F]", id="drafts"):
                yield DraftsTab(id="drafts-tab")
        yield Footer()

    def on_mount(self) -> None:
        self._fetch_data()
        self.set_interval(5, self._fetch_data)

    def action_refresh(self) -> None:
        self._fetch_data()

    def action_show_tab(self, tab_id: str) -> None:
        try:
            self.query_one(TabbedContent).active = tab_id
        except Exception:
            pass

    @work(thread=True)
    def _fetch_data(self) -> None:
        """Fetch the project report in a background thread."""
        try:
            report = self._data_manager.fetch_sync()
        except Exception as exc:
            self.call_from_thread(
                self.notify,
                f"Data refresh failed: {exc}",
                severity="error",
                timeout=4,
            )
            return
        self.call_from_thread(self._apply_report, report)

    def _apply_report(self, report: dict) -> None:
        """Apply a freshly fetched report to all tabs (called on UI thread)."""
        self._report = report
        for widget_id, widget_type in [
            ("#work-tab", WorkTab),
            ("#inbox-tab", InboxTab),
            ("#agents-tab", AgentsTab),
            ("#tasks-tab", TasksTab),
            ("#drafts-tab", DraftsTab),
        ]:
            try:
                self.query_one(widget_id, widget_type).update_data(report)
            except Exception:
                pass

    def on_task_selected(self, event: TaskSelected) -> None:
        """Open the task detail modal for the selected task."""
        self.push_screen(TaskDetailModal(event.task, self._report))
