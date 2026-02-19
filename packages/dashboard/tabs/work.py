"""Work tab — three-column kanban board (Incoming / In Progress / In Review)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, ListView
from textual.containers import Horizontal, Vertical

from ..widgets.task_card import TaskCard


class TaskSelected(Message):
    """Posted when the user selects a task card (presses Enter)."""

    def __init__(self, task: dict) -> None:
        super().__init__()
        self.task = task


class WorkColumn(Widget):
    """A single kanban column: header + scrollable list of task cards."""

    BINDINGS = [
        Binding("enter", "select_task", "Details", show=False),
    ]

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

    def action_select_task(self) -> None:
        list_view = self.query_one(ListView)
        highlighted = list_view.highlighted_child
        if isinstance(highlighted, TaskCard):
            self.post_message(TaskSelected(highlighted.task_data))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Forward list selection as a TaskSelected message."""
        if isinstance(event.item, TaskCard):
            self.post_message(TaskSelected(event.item.task_data))


class WorkTab(Widget):
    """Kanban board with Incoming, In Progress, and In Review columns."""

    DEFAULT_CSS = """
    WorkTab {
        height: 100%;
    }
    """

    def __init__(self, report: dict | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._report = report or {}

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
                classes="kanban-column",
                id="col-in-review",
            )

    def update_data(self, report: dict) -> None:
        """Replace the report and recompose the board."""
        self._report = report
        self.refresh(recompose=True)

    def on_task_selected(self, event: TaskSelected) -> None:
        """Bubble task-selection events up to the app."""
        self.post_message(event)
