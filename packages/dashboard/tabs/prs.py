"""PRs tab — list of open pull requests."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Label
from textual.containers import Vertical

from ..utils import format_age
from .base import TabBase


class PRsTab(TabBase):
    """List of open pull requests with number, title, branch, and age."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def compose(self) -> ComposeResult:
        prs = self._report.get("prs", [])
        with Vertical():
            yield Label(f" OPEN PRs ({len(prs)}) ", classes="section-header")
            if not prs:
                yield Label("No open pull requests.", classes="dim-text")
            else:
                yield DataTable(id="pr-table", classes="pr-table", cursor_type="row")

    def on_mount(self) -> None:
        self._populate_table()

    def _populate_table(self) -> None:
        prs = self._report.get("prs", [])
        try:
            table = self.query_one("#pr-table", DataTable)
        except Exception:
            return

        table.clear(columns=True)
        table.add_columns("#", "Title", "Branch", "Age", "State")

        for pr in prs:
            num = str(pr.get("number", ""))
            title = pr.get("title", "untitled")
            branch = pr.get("branch", "")
            age = format_age(pr.get("created_at"))
            state = pr.get("mergeable_state") or pr.get("state") or ""
            table.add_row(f"#{num}", title, branch, age, state)

    def action_cursor_down(self) -> None:
        try:
            self.query_one(DataTable).action_scroll_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one(DataTable).action_scroll_up()
        except Exception:
            pass

    def update_data(self, report: dict) -> None:
        """Replace the report and refresh the PR list."""
        self._report = report
        prs = report.get("prs", [])
        try:
            header = self.query_one(".section-header", Label)
            header.update(f" OPEN PRs ({len(prs)}) ")
        except Exception:
            pass
        self._populate_table()
