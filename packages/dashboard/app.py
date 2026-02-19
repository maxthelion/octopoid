"""Octopoid Dashboard — Textual TUI app.

Launch with: python -m packages.dashboard
"""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Label, TabbedContent, TabPane

from .data import DataManager
from .tabs.agents import AgentsTab
from .tabs.inbox import InboxTab
from .tabs.prs import PRsTab
from .tabs.work import TaskSelected, WorkTab


class OctopoidDashboard(App):
    """Octopoid TUI dashboard built with Textual.

    Six tabs: Work, PRs, Inbox, Agents, Done, Drafts.
    Work, PRs, Inbox, and Agents tabs are fully implemented; Done and Drafts
    show a placeholder message.
    """

    CSS_PATH = Path(__file__).parent / "styles" / "dashboard.tcss"

    TITLE = "Octopoid"
    SUB_TITLE = "Dashboard"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("w", "show_tab('work')", "Work", show=False),
        Binding("p", "show_tab('prs')", "PRs", show=False),
        Binding("i", "show_tab('inbox')", "Inbox", show=False),
        Binding("a", "show_tab('agents')", "Agents", show=False),
        Binding("d", "show_tab('done')", "Done", show=False),
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
            with TabPane("PRs [P]", id="prs"):
                yield PRsTab(id="prs-tab")
            with TabPane("Inbox [I]", id="inbox"):
                yield InboxTab(id="inbox-tab")
            with TabPane("Agents [A]", id="agents"):
                yield AgentsTab(id="agents-tab")
            with TabPane("Done [D]", id="done"):
                yield Label("Done tab — coming soon", classes="placeholder")
            with TabPane("Drafts [F]", id="drafts"):
                yield Label("Drafts tab — coming soon", classes="placeholder")
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
        try:
            self.query_one("#work-tab", WorkTab).update_data(report)
        except Exception:
            pass
        try:
            self.query_one("#prs-tab", PRsTab).update_data(report)
        except Exception:
            pass
        try:
            self.query_one("#inbox-tab", InboxTab).update_data(report)
        except Exception:
            pass
        try:
            self.query_one("#agents-tab", AgentsTab).update_data(report)
        except Exception:
            pass

    def on_task_selected(self, event: TaskSelected) -> None:
        """Handle task selection — show a brief notification for now."""
        task = event.task
        task_id = task.get("id", "???")
        title = task.get("title", "Untitled")
        self.notify(f"Selected: [{task_id}] {title}", timeout=3)
