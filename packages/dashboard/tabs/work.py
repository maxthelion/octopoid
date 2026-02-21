"""Work tab — three-column kanban board (Incoming / In Progress / In Review)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.events import Key
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, ListView
from textual.containers import Horizontal

from ..widgets.task_card import TaskCard
from .base import TabBase


class TaskSelected(Message):
    """Posted when the user selects a task card (presses Enter or clicks)."""

    def __init__(self, task: dict) -> None:
        super().__init__()
        self.task = task


class WorkColumn(Widget):
    """A single kanban column: header + scrollable list of task cards."""

    DEFAULT_CSS = """
    WorkColumn {
        width: 1fr;
        height: 100%;
        border-right: solid $panel-darken-2;
        padding: 0 1;
    }
    WorkColumn:last-of-type {
        border-right: none;
    }
    """

    def __init__(
        self,
        title: str,
        tasks: list,
        show_progress: bool = False,
        agent_map: dict | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._col_title = title
        self._tasks = tasks
        self._show_progress = show_progress
        self._agent_map = agent_map or {}

    def compose(self) -> ComposeResult:
        count = len(self._tasks)
        yield Label(f" {self._col_title} ({count}) ", classes="column-header")
        with ListView(classes="task-list"):
            for task in self._tasks:
                agent = task.get("agent")
                agent_status = "idle"
                if self._show_progress and agent:
                    agent_info = self._agent_map.get(agent)
                    if agent_info:
                        if agent_info.get("paused"):
                            agent_status = "paused"
                        else:
                            agent_status = agent_info.get("status", "idle")
                    else:
                        # Task is claimed but we have no record of the agent
                        agent_status = "orphaned"
                yield TaskCard(
                    task,
                    show_progress=self._show_progress,
                    agent_status=agent_status,
                )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Open detail modal when a task card is selected (Enter or click)."""
        if isinstance(event.item, TaskCard):
            self.post_message(TaskSelected(event.item.task_data))

    def on_key(self, event: Key) -> None:
        """Navigate between columns with left/right arrow keys."""
        if event.key not in ("left", "right"):
            return
        parent = self.parent
        if parent is None:
            return
        columns = list(parent.query(WorkColumn))
        try:
            idx = columns.index(self)
            direction = 1 if event.key == "right" else -1
            new_idx = idx + direction
            if 0 <= new_idx < len(columns):
                event.stop()
                columns[new_idx].query_one(ListView).focus()
        except Exception:
            pass


class WorkTab(TabBase):
    """Kanban board with Incoming, In Progress, and In Review columns."""

    def compose(self) -> ComposeResult:
        work = self._report.get("work", {})
        agents = self._report.get("agents", [])
        agent_map: dict = {a["name"]: a for a in agents if "name" in a}

        incoming = work.get("incoming", [])
        in_progress = work.get("in_progress", [])
        # Combine checking + in_review under "In Review" column
        in_review = list(work.get("checking", [])) + list(work.get("in_review", []))

        with Horizontal(classes="kanban-board"):
            yield WorkColumn(
                "INCOMING",
                incoming,
                classes="kanban-column",
                id="col-incoming",
            )
            yield WorkColumn(
                "IN PROGRESS",
                in_progress,
                show_progress=True,
                agent_map=agent_map,
                classes="kanban-column",
                id="col-in-progress",
            )
            yield WorkColumn(
                "IN REVIEW",
                in_review,
                show_progress=True,
                agent_map=agent_map,
                classes="kanban-column",
                id="col-in-review",
            )

    def on_mount(self) -> None:
        """Focus the first column's task list on initial mount."""
        self._focus_first_column()

    def on_show(self) -> None:
        """Restore focus to the first column when the tab becomes active."""
        self._focus_first_column()

    def _focus_first_column(self) -> None:
        try:
            columns = list(self.query(WorkColumn))
            if columns:
                columns[0].query_one(ListView).focus()
        except Exception:
            pass

    def _refresh(self) -> None:
        self.refresh(recompose=True)
