"""Tasks tab — nested Done / Failed / Proposed sub-tabs."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import DataTable, Label, TabbedContent, TabPane
from textual.containers import Vertical

from .done import DoneTab
from .work import TaskSelected


class FailedTab(Widget):
    """Filtered view of tasks that ended in the failed queue (last 7 days)."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    DEFAULT_CSS = """
    FailedTab {
        height: 100%;
    }
    """

    def __init__(self, report: dict | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._report = report or {}
        self._tasks: list[dict] = [
            t for t in self._report.get("done_tasks", [])
            if t.get("final_queue") == "failed"
        ]

    def _format_age(self, iso_str: str | None) -> str:
        if not iso_str:
            return ""
        try:
            dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
            if dt.tzinfo:
                dt = dt.replace(tzinfo=None)
            delta = datetime.now() - dt
            secs = delta.total_seconds()
            if secs < 0:
                return "now"
            if secs < 60:
                return f"{int(secs)}s"
            if secs < 3600:
                return f"{int(secs // 60)}m"
            if secs < 86400:
                return f"{int(secs // 3600)}h"
            return f"{int(secs // 86400)}d"
        except (ValueError, TypeError):
            return ""

    def compose(self) -> ComposeResult:
        n = len(self._tasks)
        with Vertical():
            yield Label(
                f" FAILED TASKS ({n}) — last 7 days ",
                classes="section-header",
                id="failed-header",
            )
            yield DataTable(id="failed-table", cursor_type="row", classes="done-table")

    def on_mount(self) -> None:
        self._populate_table()

    def _populate_table(self) -> None:
        try:
            table = self.query_one("#failed-table", DataTable)
        except Exception:
            return
        table.clear(columns=True)
        table.add_columns("ID", "Title", "Age", "Turns", "Agent")
        for task in self._tasks:
            task_id = (task.get("id") or "")[:8]
            title = task.get("title") or "untitled"
            agent = task.get("agent") or ""
            turns = int(task.get("turns") or 0)
            turn_limit = int(task.get("turn_limit") or 100)
            completed_at = task.get("completed_at")
            age = self._format_age(completed_at)
            table.add_row(
                task_id,
                title[:50],
                age,
                f"{turns}/{turn_limit}",
                agent[:12] if agent else "",
            )

    def action_cursor_down(self) -> None:
        try:
            self.query_one(DataTable).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one(DataTable).action_cursor_up()
        except Exception:
            pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_idx = event.cursor_row
        if 0 <= row_idx < len(self._tasks):
            self.post_message(TaskSelected(self._tasks[row_idx]))

    def update_data(self, report: dict) -> None:
        self._report = report
        self._tasks = [
            t for t in report.get("done_tasks", [])
            if t.get("final_queue") == "failed"
        ]
        n = len(self._tasks)
        try:
            header = self.query_one("#failed-header", Label)
            header.update(f" FAILED TASKS ({n}) — last 7 days ")
        except Exception:
            pass
        self._populate_table()


class TasksTab(Widget):
    """Tasks tab with nested Done / Failed / Proposed sub-tabs."""

    DEFAULT_CSS = """
    TasksTab {
        height: 100%;
    }
    """

    def __init__(self, report: dict | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._report = report or {}

    def compose(self) -> ComposeResult:
        with TabbedContent(id="tasks-inner-tabs"):
            with TabPane("Done", id="tasks-done"):
                yield DoneTab(id="done-inner-tab")
            with TabPane("Failed", id="tasks-failed"):
                yield FailedTab(id="failed-inner-tab")
            with TabPane("Proposed", id="tasks-proposed"):
                yield Label(
                    "Proposed tasks — coming soon",
                    classes="placeholder",
                )

    def update_data(self, report: dict) -> None:
        self._report = report
        for widget_id, widget_type in [
            ("#done-inner-tab", DoneTab),
            ("#failed-inner-tab", FailedTab),
        ]:
            try:
                self.query_one(widget_id, widget_type).update_data(report)
            except Exception:
                pass
