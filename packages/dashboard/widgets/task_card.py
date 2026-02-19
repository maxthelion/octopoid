"""Task card widget for display in kanban columns."""

from datetime import datetime

from textual.app import ComposeResult
from textual.widgets import Label, ListItem
from textual.containers import Horizontal, Vertical

from .status_badge import StatusBadge


def _progress_bar(value: int, total: int, width: int = 10) -> str:
    """Render a Unicode block progress bar string."""
    if total <= 0:
        return f"[{'░' * width}] 0/0t"
    filled = min(width, int(width * value / total))
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {value}/{total}t"


def _format_duration(iso_str: str | None) -> str | None:
    """Format elapsed time since an ISO timestamp as a human-readable string.

    Returns strings like "2h 15m", "3d 4h", or "45m". Returns None if the
    timestamp is missing or cannot be parsed.
    """
    if not iso_str:
        return None
    try:
        cleaned = str(iso_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        delta = datetime.now() - dt
        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            return None
        days = delta.days
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        if days > 0:
            return f"{days}d {hours}h"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    except (ValueError, TypeError):
        return None


class TaskCard(ListItem):
    """A single task displayed as a card in a kanban column.

    Shows: task ID, priority badge, title, and optionally extra detail:
    - show_progress=True (In Progress): agent name, status badge, turns progress bar.
    - show_review=True (In Review): agent name and time-in-review duration.
    """

    def __init__(
        self,
        task: dict,
        show_progress: bool = False,
        show_review: bool = False,
        agent_status: str = "idle",
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.task_data = task
        self.show_progress = show_progress
        self.show_review = show_review
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
                yield Label(f"  {agent_name}", classes="task-agent dim")
                yield Label(
                    _progress_bar(turns, turn_limit),
                    classes="task-progress",
                )
            elif self.show_review and agent:
                agent_name = (agent or "")[:12]
                submitted_at = task.get("submitted_at")
                duration = _format_duration(submitted_at)
                yield Label(f"  {agent_name}", classes="task-agent dim")
                if duration:
                    yield Label(f"  ⏱ {duration}", classes="task-review-duration")
