"""Agents tab — master-detail view of all configured agents."""

from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView
from textual.containers import Horizontal, Vertical, VerticalScroll

from ..widgets.status_badge import StatusBadge


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


class AgentItem(ListItem):
    """A single agent row in the agent list panel."""

    def __init__(self, agent: dict, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._agent = agent

    @property
    def agent_data(self) -> dict:
        return self._agent

    def compose(self) -> ComposeResult:
        agent = self._agent
        name = agent.get("name", "?")
        status = agent.get("status", "idle")
        paused = agent.get("paused", False)

        badge_status = "paused" if paused else status

        with Horizontal(classes="agent-list-item"):
            yield Label(name, classes="agent-name")
            yield StatusBadge(badge_status)


class AgentDetail(Widget):
    """Detail pane showing full info for the currently selected agent."""

    DEFAULT_CSS = """
    AgentDetail {
        height: 100%;
        padding: 0 1;
    }
    """

    def __init__(self, agent: dict | None = None, report: dict | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._agent = agent
        self._report = report or {}

    def compose(self) -> ComposeResult:
        if not self._agent:
            yield Label("No agent selected.", classes="dim-text")
            return

        agent = self._agent
        report = self._report
        name = agent.get("name", "?")
        role = agent.get("role", "?")
        status = agent.get("status", "idle")
        paused = agent.get("paused", False)
        current_task_id = agent.get("current_task")
        last_started = agent.get("last_started")
        recent_tasks = agent.get("recent_tasks", [])
        notes = agent.get("notes")

        with VerticalScroll():
            # Agent name header
            yield Label(name, classes="agent-detail-name")

            # Role
            yield Label(f"Role: {role}", classes="agent-detail-row")

            # Status
            if paused:
                status_text = "PAUSED"
                status_class = "status--paused"
            elif status == "running":
                age = _format_age(last_started)
                age_suffix = f" · {age} elapsed" if age else ""
                # Look up turns from work data
                turns_text = ""
                if current_task_id:
                    for cat in report.get("work", {}).values():
                        if isinstance(cat, list):
                            for t in cat:
                                if t.get("id") == current_task_id:
                                    turns = t.get("turns", 0)
                                    turn_limit = t.get("turn_limit", 100)
                                    if turns:
                                        turns_text = f" · {turns}/{turn_limit} turns"
                                    break
                status_text = f"RUNNING{age_suffix}{turns_text}"
                status_class = "status--running"
            elif status.startswith("idle("):
                reason = status[5:-1] if status.endswith(")") else status[5:]
                status_text = f"BLOCKED · {reason}"
                status_class = "status--blocked"
            else:
                age = _format_age(last_started)
                age_suffix = f" · last run {age} ago" if age else ""
                status_text = f"IDLE{age_suffix}"
                status_class = "status--idle"

            yield Label(f"Status: {status_text}", classes=f"agent-detail-row {status_class}")

            # Blueprint metrics from health report
            health = report.get("health", {})
            blueprints = health.get("blueprints", {})
            if name in blueprints:
                bp = blueprints[name]
                running = bp.get("running_instances", 0)
                max_inst = bp.get("max_instances", "?")
                idle_cap = bp.get("idle_capacity", 0)
                yield Label(
                    f"Instances: {running}/{max_inst}  idle capacity: {idle_cap}",
                    classes="agent-detail-row dim-text",
                )

            yield Label("")  # spacer

            # Current task section
            yield Label("CURRENT TASK", classes="detail-section-header")
            if current_task_id:
                # Look up task details in work data
                task_info = None
                for cat in report.get("work", {}).values():
                    if isinstance(cat, list):
                        for t in cat:
                            if t.get("id") == current_task_id:
                                task_info = t
                                break

                if task_info:
                    title = task_info.get("title", "untitled")
                    yield Label(f"{current_task_id} {title}", classes="agent-detail-row")
                    branch = task_info.get("branch", "")
                    if branch:
                        yield Label(f"Branch: {branch}", classes="agent-detail-row dim-text")
                    commits = task_info.get("commits", 0)
                    yield Label(f"Commits: {commits}", classes="agent-detail-row dim-text")
                else:
                    yield Label(f"Task: {current_task_id}", classes="agent-detail-row")
            else:
                yield Label("(none)", classes="agent-detail-row dim-text")

            yield Label("")  # spacer

            # Recent work section
            yield Label("RECENT WORK", classes="detail-section-header")
            if recent_tasks:
                for rt in recent_tasks[:5]:
                    tid = (rt.get("id") or "?")[:8]
                    rtitle = rt.get("title", "untitled")
                    queue = rt.get("queue", "")
                    pr_num = rt.get("pr_number")
                    check = "✓" if queue == "done" else "○"
                    pr_text = f"  PR #{pr_num}" if pr_num else ""
                    css = "status--idle" if queue == "done" else "dim-text"
                    yield Label(
                        f"{check} {tid} {rtitle}{pr_text}",
                        classes=f"agent-detail-row {css}",
                    )
            else:
                yield Label("(no recent tasks)", classes="agent-detail-row dim-text")

            # Notes section
            if notes:
                yield Label("")  # spacer
                yield Label("NOTES", classes="detail-section-header")
                yield Label(notes, classes="agent-detail-row dim-text")

    def update_agent(self, agent: dict | None, report: dict) -> None:
        """Switch to a new agent and recompose the detail pane."""
        self._agent = agent
        self._report = report
        self.refresh(recompose=True)


class AgentsTab(Widget):
    """Master-detail agents view: list on left, detail on right."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    DEFAULT_CSS = """
    AgentsTab {
        height: 100%;
    }
    """

    def __init__(self, report: dict | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._report = report or {}

    def compose(self) -> ComposeResult:
        agents = self._report.get("agents", [])
        selected = agents[0] if agents else None

        with Horizontal(classes="agents-layout"):
            with Vertical(classes="agent-list-panel", id="agent-list-panel"):
                yield Label(" AGENTS ", classes="section-header")
                with ListView(id="agent-listview", classes="agent-listview"):
                    for agent in agents:
                        yield AgentItem(agent)
            yield AgentDetail(
                agent=selected,
                report=self._report,
                classes="agent-detail-panel",
                id="agent-detail",
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Update the detail pane when an agent is selected."""
        if isinstance(event.item, AgentItem):
            detail = self.query_one("#agent-detail", AgentDetail)
            detail.update_agent(event.item.agent_data, self._report)

    def action_cursor_down(self) -> None:
        try:
            lv = self.query_one("#agent-listview", ListView)
            lv.action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            lv = self.query_one("#agent-listview", ListView)
            lv.action_cursor_up()
        except Exception:
            pass

    def update_data(self, report: dict) -> None:
        """Replace the report and recompose the agents view."""
        self._report = report
        self.refresh(recompose=True)
