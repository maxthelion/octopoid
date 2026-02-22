"""Agents tab — two-tier TabbedContent: Flow Agents and Background Agents."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, TabbedContent, TabPane
from textual.containers import Horizontal, Vertical, VerticalScroll

from ..utils import format_age
from ..widgets.status_badge import StatusBadge
from .base import TabBase

# Roles that belong to the "flow" agents tab (claim tasks from queues).
# All other roles are treated as background agents.
_FLOW_ROLES: frozenset[str] = frozenset({"implement", "gatekeeper"})


class AgentItem(ListItem):
    """A single agent or job row in the list panel."""

    def __init__(self, agent: dict, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._agent = agent

    @property
    def agent_data(self) -> dict:
        return self._agent

    def compose(self) -> ComposeResult:
        agent = self._agent
        name = agent.get("name", "?")
        agent_type = agent.get("agent_type", "flow")

        with Horizontal(classes="agent-list-item"):
            yield Label(name, classes="agent-name")
            if agent_type == "flow":
                status = agent.get("status", "idle")
                paused = agent.get("paused", False)
                badge_status = "paused" if paused else status
                yield StatusBadge(badge_status)
            else:
                # Background agent or job — show interval
                interval = agent.get("interval_seconds") or agent.get("interval", 0)
                if interval >= 3600:
                    interval_str = f"{interval // 3600}h"
                elif interval >= 60:
                    interval_str = f"{interval // 60}m"
                else:
                    interval_str = f"{interval}s"
                yield Label(interval_str, classes="agent-job-interval dim-text")


class AgentDetail(Widget):
    """Detail pane showing full info for the currently selected agent or job."""

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

        agent_type = self._agent.get("agent_type", "flow")
        if agent_type == "job":
            yield from self._compose_job_detail()
        elif agent_type == "background":
            yield from self._compose_background_detail()
        else:
            yield from self._compose_flow_detail()

    def _compose_flow_detail(self) -> ComposeResult:
        """Render detail for a flow agent (implementer, gatekeeper, etc.)."""
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

            # Type indicator
            yield Label("Type: flow agent", classes="agent-detail-row dim-text")

            # Role
            yield Label(f"Role: {role}", classes="agent-detail-row")

            # Status
            if paused:
                status_text = "PAUSED"
                status_class = "status--paused"
            elif status == "running":
                age = format_age(last_started)
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
                age = format_age(last_started)
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

    def _compose_background_detail(self) -> ComposeResult:
        """Render detail for a background agent (non-flow, runs periodically)."""
        agent = self._agent
        name = agent.get("name", "?")
        role = agent.get("role", "?")
        interval_seconds = agent.get("interval_seconds")
        last_run = agent.get("last_run")
        notes = agent.get("notes")

        with VerticalScroll():
            yield Label(name, classes="agent-detail-name")

            yield Label("Type: background agent", classes="agent-detail-row dim-text")
            yield Label(f"Role: {role}", classes="agent-detail-row")

            yield Label("")  # spacer

            yield Label("SCHEDULE", classes="detail-section-header")
            if interval_seconds is not None:
                if interval_seconds >= 3600:
                    interval_str = f"{interval_seconds // 3600}h ({interval_seconds}s)"
                elif interval_seconds >= 60:
                    interval_str = f"{interval_seconds // 60}m ({interval_seconds}s)"
                else:
                    interval_str = f"{interval_seconds}s"
                yield Label(f"Interval: {interval_str}", classes="agent-detail-row")
            else:
                yield Label("Interval: (unknown)", classes="agent-detail-row dim-text")

            yield Label("")  # spacer

            yield Label("RUN STATUS", classes="detail-section-header")
            if last_run:
                age = format_age(last_run)
                age_text = f" ({age} ago)" if age else ""
                yield Label(f"Last run: {last_run[:19]}{age_text}", classes="agent-detail-row")
            else:
                yield Label("Last run: (never)", classes="agent-detail-row dim-text")

            if notes:
                yield Label("")  # spacer
                yield Label("RECENT OUTPUT", classes="detail-section-header")
                yield Label(notes, classes="agent-detail-row dim-text")

    def _compose_job_detail(self) -> ComposeResult:
        """Render detail for a scheduler background job."""
        agent = self._agent
        name = agent.get("name", "?")
        job_type = agent.get("job_type", "script")
        group = agent.get("group", "remote")
        interval = agent.get("interval", 0)
        last_run = agent.get("last_run")
        next_run = agent.get("next_run")

        with VerticalScroll():
            # Job name header
            yield Label(name, classes="agent-detail-name")

            # Type indicator
            yield Label("Type: scheduler job", classes="agent-detail-row dim-text")

            yield Label("")  # spacer

            # Schedule section
            yield Label("SCHEDULE", classes="detail-section-header")

            # Interval
            if interval >= 3600:
                interval_str = f"{interval // 3600}h ({interval}s)"
            elif interval >= 60:
                interval_str = f"{interval // 60}m ({interval}s)"
            else:
                interval_str = f"{interval}s"
            yield Label(f"Interval: {interval_str}", classes="agent-detail-row")

            # Execution type and group
            yield Label(f"Exec type: {job_type}", classes="agent-detail-row dim-text")
            yield Label(f"Group: {group}", classes="agent-detail-row dim-text")

            yield Label("")  # spacer

            # Run history section
            yield Label("RUN STATUS", classes="detail-section-header")

            if last_run:
                age = format_age(last_run)
                age_text = f" ({age} ago)" if age else ""
                yield Label(f"Last run: {last_run[:19]}{age_text}", classes="agent-detail-row")
            else:
                yield Label("Last run: (never)", classes="agent-detail-row dim-text")

            if next_run:
                from datetime import datetime
                try:
                    next_dt = datetime.fromisoformat(next_run)
                    now = datetime.now()
                    diff = (next_dt - now).total_seconds()
                    if diff <= 0:
                        due_text = "due now"
                        due_class = "status--running"
                    elif diff < 60:
                        due_text = f"in {int(diff)}s"
                        due_class = "agent-detail-row"
                    elif diff < 3600:
                        due_text = f"in {int(diff // 60)}m"
                        due_class = "agent-detail-row"
                    else:
                        due_text = f"in {int(diff // 3600)}h"
                        due_class = "agent-detail-row"
                    yield Label(f"Next run: {due_text}", classes=f"agent-detail-row {due_class}")
                except (ValueError, TypeError):
                    yield Label(f"Next run: {next_run[:19]}", classes="agent-detail-row dim-text")
            else:
                yield Label("Next run: (unknown)", classes="agent-detail-row dim-text")

    def update_agent(self, agent: dict | None, report: dict) -> None:
        """Switch to a new agent and recompose the detail pane."""
        self._agent = agent
        self._report = report
        self.refresh(recompose=True)


class AgentsTab(TabBase):
    """Agents view with two sub-tabs: Flow Agents and Background Agents.

    Flow Agents: implementer/gatekeeper agents that claim tasks from queues.
    Background Agents: autonomous agents that run on a schedule.
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def compose(self) -> ComposeResult:
        agents = self._report.get("agents", [])

        flow_agents = [a for a in agents if a.get("role") in _FLOW_ROLES]
        # Background: non-flow agents from agents.yaml (not scheduler jobs)
        bg_agents = [a for a in agents if a.get("role") not in _FLOW_ROLES]
        bg_items = bg_agents

        # Default selection: first flow agent, or first background item if no flow agents
        selected = flow_agents[0] if flow_agents else (bg_items[0] if bg_items else None)

        with Horizontal(classes="agents-layout"):
            with Vertical(classes="agent-list-panel", id="agent-list-panel"):
                with TabbedContent(id="agent-sub-tabs"):
                    with TabPane("Flow Agents", id="tab-flow"):
                        with ListView(id="flow-listview", classes="agent-listview"):
                            for agent in flow_agents:
                                yield AgentItem(agent)

                    with TabPane("Background Agents", id="tab-background"):
                        with ListView(id="bg-listview", classes="agent-listview"):
                            for item in bg_items:
                                yield AgentItem(item)

            yield AgentDetail(
                agent=selected,
                report=self._report,
                classes="agent-detail-panel",
                id="agent-detail",
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Update the detail pane when an agent or job is selected."""
        if isinstance(event.item, AgentItem):
            detail = self.query_one("#agent-detail", AgentDetail)
            detail.update_agent(event.item.agent_data, self._report)

    def _active_listview_id(self) -> str:
        """Return the CSS selector for the listview in the currently active sub-tab."""
        try:
            tabs = self.query_one("#agent-sub-tabs", TabbedContent)
            if tabs.active == "tab-background":
                return "#bg-listview"
        except Exception:
            pass
        return "#flow-listview"

    def action_cursor_down(self) -> None:
        try:
            lv = self.query_one(self._active_listview_id(), ListView)
            lv.action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            lv = self.query_one(self._active_listview_id(), ListView)
            lv.action_cursor_up()
        except Exception:
            pass

    def _refresh(self) -> None:
        self.refresh(recompose=True)
