"""Task card widget for display in kanban columns."""

from textual.app import ComposeResult
from textual.widgets import Label, ListItem
from textual.containers import Horizontal, Vertical

from ..utils import time_ago
from .status_badge import StatusBadge


def _progress_bar(value: int, total: int, width: int = 10) -> str:
    """Render a Unicode block progress bar string."""
    if total <= 0:
        return f"[{'░' * width}] 0/0t"
    filled = min(width, int(width * value / total))
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {value}/{total}t"


class TaskCard(ListItem):
    """A single task displayed as a card in a kanban column.

    Shows: task ID, priority badge, title, and (for In Progress) agent name,
    status badge, and turns progress bar.
    """

    def __init__(
        self,
        task: dict,
        show_progress: bool = False,
        agent_status: str = "idle",
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.task_data = task
        self.show_progress = show_progress
        self.agent_status = agent_status

    def compose(self) -> ComposeResult:
        task = self.task_data
        priority = task.get("priority") or "P2"
        task_id = task.get("id") or "???"
        title = task.get("title") or "Untitled"
        agent = task.get("agent")
        turns = int(task.get("turns") or 0)
        turn_limit = int(task.get("turn_limit") or 100)

        priority_class = f"priority-{priority.lower()}"

        with Vertical(classes="task-card-inner"):
            with Horizontal(classes="task-card-header"):
                yield Label(task_id, classes=f"task-id {priority_class}")
                yield Label(f" [{priority}]", classes=f"priority-badge {priority_class}")
                if self.show_progress and agent:
                    yield StatusBadge(self.agent_status, classes="task-status")
            yield Label(title, classes="task-title")
            if self.show_progress and agent:
                agent_name = (agent or "")[:12]
                claimed_ago = time_ago(task.get("claimed_at"))
                agent_label = f"  {agent_name}"
                if claimed_ago:
                    agent_label += f"  {claimed_ago}"
                yield Label(agent_label, classes="task-agent dim")
                yield Label(
                    _progress_bar(turns, turn_limit),
                    classes="task-progress",
                )
