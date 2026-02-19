"""TaskDetail widget and TaskDetailModal screen for displaying full task info."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Label
from textual.containers import Container, VerticalScroll

from .status_badge import StatusBadge


class TaskDetail(Widget):
    """Full-detail panel for a single task.

    Shows: ID, title, role, priority, agent, agent status badge, turns,
    commits, PR link, and (for done tasks) outcome / merge info.
    """

    DEFAULT_CSS = """
    TaskDetail {
        height: 100%;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        task: dict,
        report: dict | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._task = task
        self._report = report or {}

    def compose(self) -> ComposeResult:
        task = self._task
        report = self._report

        task_id = task.get("id") or "???"
        title = task.get("title") or "Untitled"
        role = task.get("role") or "?"
        priority = task.get("priority") or "?"
        agent = task.get("agent")
        turns = int(task.get("turns") or 0)
        turn_limit = int(task.get("turn_limit") or 100)
        commits = int(task.get("commits") or 0)
        pr_number = task.get("pr_number")
        final_queue = task.get("final_queue", "")
        accepted_by = task.get("accepted_by") or ""
        completed_at = task.get("completed_at") or ""

        p_class = f"priority-{priority.lower()}" if priority else ""

        with VerticalScroll():
            # Task ID and title
            yield Label(task_id, classes=f"agent-detail-name {p_class}")
            yield Label(title, classes="task-title agent-detail-row")
            yield Label("")  # spacer

            # Details section
            yield Label("DETAILS", classes="detail-section-header")
            yield Label(f"Role:      {role}", classes="agent-detail-row dim-text")
            yield Label(f"Priority:  {priority}", classes=f"agent-detail-row {p_class}")

            if agent:
                # Look up live agent status from report
                agent_info = next(
                    (a for a in report.get("agents", []) if a.get("name") == agent),
                    None,
                )
                if agent_info:
                    paused = agent_info.get("paused", False)
                    raw_status = agent_info.get("status", "idle")
                    badge_status = "paused" if paused else raw_status
                else:
                    badge_status = "idle"

                yield Label(f"Agent:     {agent}", classes="agent-detail-row")
                yield StatusBadge(badge_status, classes="detail-agent-badge")
            else:
                yield Label("Agent:     (none)", classes="agent-detail-row dim-text")

            yield Label("")  # spacer

            # Progress section
            yield Label("PROGRESS", classes="detail-section-header")
            yield Label(f"Turns:     {turns}/{turn_limit}", classes="agent-detail-row dim-text")
            yield Label(f"Commits:   {commits}", classes="agent-detail-row dim-text")
            if pr_number:
                yield Label(f"PR:        #{pr_number}", classes="agent-detail-row")

            # Outcome section (done/failed/recycled tasks only)
            if final_queue:
                yield Label("")  # spacer
                yield Label("OUTCOME", classes="detail-section-header")
                if final_queue == "done":
                    yield Label("Status:    ✓ Done", classes="agent-detail-row status--running")
                elif final_queue == "failed":
                    yield Label("Status:    ✗ Failed", classes="agent-detail-row status--blocked")
                elif final_queue == "recycled":
                    yield Label("Status:    ♻ Recycled", classes="agent-detail-row status--paused")
                if accepted_by:
                    yield Label(
                        f"Merged by: {accepted_by}",
                        classes="agent-detail-row dim-text",
                    )
                if completed_at:
                    yield Label(
                        f"Completed: {completed_at[:19]}",
                        classes="agent-detail-row dim-text",
                    )


class TaskDetailModal(ModalScreen):
    """Modal overlay showing full task detail. Press Escape to close."""

    BINDINGS = [Binding("escape", "dismiss", "Close", show=True)]

    DEFAULT_CSS = """
    TaskDetailModal {
        align: center middle;
    }
    #detail-dialog {
        width: 80%;
        height: 80%;
        background: #16213e;
        border: solid #4fc3f7;
        padding: 1 2;
    }
    .modal-title {
        color: #4fc3f7;
        text-style: bold;
        text-align: center;
        width: 100%;
        margin-bottom: 1;
    }
    .modal-hint {
        color: #616161;
        text-align: center;
        width: 100%;
        margin-top: 1;
    }
    .detail-agent-badge {
        margin-left: 11;
    }
    """

    def __init__(
        self,
        task: dict,
        report: dict | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._task = task
        self._report = report or {}

    def compose(self) -> ComposeResult:
        with Container(id="detail-dialog"):
            yield Label("Task Detail  [Esc to close]", classes="modal-title")
            yield TaskDetail(self._task, self._report)
