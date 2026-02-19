"""Done tab — completed, failed, and recycled tasks from the last 7 days."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import DataTable, Label
from textual.containers import Vertical

from .work import TaskSelected


def _format_age(iso_str: str | None) -> str:
    """Format an ISO timestamp as a human-readable age like '2h', '15m'."""
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


def _summary_text(done_tasks: list[dict]) -> str:
    """Build a summary string like '5 done · 2 failed · 1 recycled'."""
    done_count = sum(1 for t in done_tasks if t.get("final_queue") == "done")
    failed_count = sum(1 for t in done_tasks if t.get("final_queue") == "failed")
    recycled_count = sum(1 for t in done_tasks if t.get("final_queue") == "recycled")
    parts = [f"{done_count} done"]
    if recycled_count:
        parts.append(f"{recycled_count} recycled")
    if failed_count:
        parts.append(f"{failed_count} failed")
    return " · ".join(parts)


class DoneTab(Widget):
    """Scrollable table of completed/failed/recycled tasks from the last 7 days."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    DEFAULT_CSS = """
    DoneTab {
        height: 100%;
    }
    """

    def __init__(self, report: dict | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._report = report or {}
        self._tasks: list[dict] = self._report.get("done_tasks", [])

    def compose(self) -> ComposeResult:
        summary = _summary_text(self._tasks)
        n = len(self._tasks)
        with Vertical():
            yield Label(
                f" COMPLETED WORK ({n}) — last 7 days  ·  {summary} ",
                classes="section-header",
                id="done-header",
            )
            yield DataTable(id="done-table", cursor_type="row", classes="done-table")

    def on_mount(self) -> None:
        self._populate_table()

    def _populate_table(self) -> None:
        try:
            table = self.query_one("#done-table", DataTable)
        except Exception:
            return

        table.clear(columns=True)
        table.add_columns("", "ID", "Title", "Age", "Turns", "Cmts", "Merge", "Agent")

        for task in self._tasks:
            final_queue = task.get("final_queue", "done")
            task_id = (task.get("id") or "")[:8]
            title = task.get("title") or "untitled"
            agent = task.get("agent") or ""
            turns = int(task.get("turns") or 0)
            turn_limit = int(task.get("turn_limit") or 100)
            commits = int(task.get("commits") or 0)
            accepted_by = task.get("accepted_by") or ""
            completed_at = task.get("completed_at")
            role = task.get("role", "")
            is_orch = role in ("orchestrator_impl", "breakdown", "recycler", "inbox_poller")

            # Status icon
            if final_queue == "failed":
                icon = Text("✗", style="bold #ef5350")
            elif final_queue == "recycled":
                icon = Text("♻", style="bold #ffa726")
            else:
                icon = Text("✓", style="bold #66bb6a")

            # ID — show ORCH badge for orchestrator tasks
            id_display = f"ORCH {task_id}" if is_orch else task_id

            age = _format_age(completed_at)
            turns_text = f"{turns}/{turn_limit}"

            # Merge / outcome column
            if accepted_by:
                merge_display: str | Text = accepted_by[:10]
            elif final_queue == "failed":
                merge_display = Text("failed", style="#ef5350")
            elif final_queue == "recycled":
                merge_display = Text("recycled", style="#ffa726")
            else:
                merge_display = ""

            table.add_row(
                icon,
                id_display,
                title[:45],
                age,
                turns_text,
                str(commits),
                merge_display,
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
        """Post TaskSelected when the user presses Enter on a done task row."""
        row_idx = event.cursor_row
        if 0 <= row_idx < len(self._tasks):
            self.post_message(TaskSelected(self._tasks[row_idx]))

    def update_data(self, report: dict) -> None:
        """Replace the report and refresh the done table."""
        self._report = report
        self._tasks = report.get("done_tasks", [])
        summary = _summary_text(self._tasks)
        n = len(self._tasks)
        try:
            header = self.query_one("#done-header", Label)
            header.update(f" COMPLETED WORK ({n}) — last 7 days  ·  {summary} ")
        except Exception:
            pass
        self._populate_table()
