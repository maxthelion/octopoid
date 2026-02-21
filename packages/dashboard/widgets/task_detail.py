"""TaskDetail widget and TaskDetailModal screen for displaying full task info."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Label, TabbedContent, TabPane
from textual.containers import Container, VerticalScroll

from .status_badge import StatusBadge


def _get_repo_root() -> Path:
    """Return the repo root (3 levels above packages/dashboard/widgets/)."""
    return Path(__file__).resolve().parents[3]


def _get_base_branch() -> str:
    """Get the repo base branch from orchestrator config."""
    try:
        import sys
        repo_root = _get_repo_root()
        repo_str = str(repo_root)
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)
        from orchestrator.config import get_base_branch
        return get_base_branch()
    except Exception:
        return "feature/client-server-architecture"


def _fetch_tab_content(task_id: str, tab_index: int) -> str:
    """Fetch content for the given tab index.

    May block briefly (git diff, file I/O). Designed to be called from a
    background thread via @work(thread=True).
    """
    repo_root = _get_repo_root()
    runtime_dir = repo_root / ".octopoid" / "runtime" / "tasks" / task_id

    if tab_index == 0:  # Diff
        worktree = runtime_dir / "worktree"
        if not worktree.exists():
            return "(no diff available — worktree not found)"
        try:
            base_branch = _get_base_branch()
            result = subprocess.run(
                ["git", "diff", "--stat", f"origin/{base_branch}...HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=worktree,
            )
            output = result.stdout.strip()
            if not output:
                return "(no diff available — no commits yet)"
            return output
        except subprocess.TimeoutExpired:
            return "(git diff timed out)"
        except Exception as e:
            return f"(error running git diff: {e})"

    elif tab_index == 1:  # Desc
        task_file = repo_root / ".octopoid" / "tasks" / f"{task_id}.md"
        if task_file.exists():
            try:
                return task_file.read_text()
            except OSError:
                pass
        # Fallback: extract ## Task Description section from prompt.md
        prompt_file = runtime_dir / "prompt.md"
        if prompt_file.exists():
            try:
                lines = prompt_file.read_text().splitlines()
                in_section = False
                section_lines: list[str] = []
                for line in lines:
                    if "## Task Description" in line:
                        in_section = True
                    elif in_section and line.startswith("## "):
                        break
                    elif in_section:
                        section_lines.append(line)
                if section_lines:
                    return "\n".join(section_lines)
                return "\n".join(lines)
            except OSError:
                pass
        return "(task description not found)"

    elif tab_index == 2:  # Result
        result_file = runtime_dir / "result.json"
        if not result_file.exists():
            return "(no result yet)"
        try:
            data = json.loads(result_file.read_text())
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"(error reading result.json: {e})"

    elif tab_index == 3:  # Logs
        for log_name in ("stdout.log", "stderr.log"):
            log_file = runtime_dir / log_name
            if log_file.exists():
                try:
                    content = log_file.read_text()
                    if content.strip():
                        return content
                except OSError:
                    pass
        return "(no logs available)"

    return "(unknown tab)"


# Maps tab pane ID -> tab index for content loading
_TAB_ID_TO_INDEX: dict[str, int] = {
    "tab-diff": 0,
    "tab-desc": 1,
    "tab-result": 2,
    "tab-logs": 3,
}
_TAB_CONTENT_IDS = ["content-diff", "content-desc", "content-result", "content-logs"]


class TaskDetail(Widget):
    """Full-detail panel for a single task.

    Shows: ID, title, role, priority, agent, agent status badge, turns,
    commits, PR link, and (for done tasks) outcome / merge info.
    """

    def __init__(
        self,
        task: dict,
        report: dict | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._task_data = task
        self._report = report or {}

    def compose(self) -> ComposeResult:
        task = self._task_data
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
    """Modal overlay showing full task detail with tabbed content views.

    Top section shows compact metadata (ID, title, priority, agent, turns).
    Below is a tabbed content area with Diff, Desc, Result, and Logs views.
    Content loads in a background thread to avoid blocking the UI.
    Press Escape to close.
    """

    BINDINGS = [Binding("escape", "dismiss", "Close", show=True)]

    def __init__(
        self,
        task: dict,
        report: dict | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._task_data = task
        self._report = report or {}
        self._loaded_tabs: set[int] = set()

    def compose(self) -> ComposeResult:
        task = self._task_data
        task_id = task.get("id") or "???"
        title = task.get("title") or "Untitled"
        priority = task.get("priority") or "?"
        agent = task.get("agent") or "(none)"
        turns = int(task.get("turns") or 0)
        turn_limit = int(task.get("turn_limit") or 100)
        pr_number = task.get("pr_number")

        p_class = f"priority-{priority.lower()}" if priority else ""

        with Container(id="detail-dialog"):
            yield Label("Task Detail  [Esc to close]", classes="modal-title")

            # Compact metadata summary
            with Container(id="detail-meta"):
                yield Label(
                    f"{task_id[:8]}  {title}",
                    classes=f"detail-meta-title {p_class}",
                )
                meta_parts = [
                    f"Priority: {priority}",
                    f"Agent: {agent}",
                    f"Turns: {turns}/{turn_limit}",
                ]
                if pr_number:
                    meta_parts.append(f"PR: #{pr_number}")
                yield Label("  |  ".join(meta_parts), classes="detail-meta-row")

            # Tabbed content area
            with TabbedContent(id="detail-tabs"):
                with TabPane("Diff", id="tab-diff"):
                    with VerticalScroll():
                        yield Label("Loading...", id="content-diff", classes="tab-content-label")
                with TabPane("Desc", id="tab-desc"):
                    with VerticalScroll():
                        yield Label("Loading...", id="content-desc", classes="tab-content-label")
                with TabPane("Result", id="tab-result"):
                    with VerticalScroll():
                        yield Label("Loading...", id="content-result", classes="tab-content-label")
                with TabPane("Logs", id="tab-logs"):
                    with VerticalScroll():
                        yield Label("Loading...", id="content-logs", classes="tab-content-label")

    def on_mount(self) -> None:
        """Load the first tab (Diff) immediately on mount."""
        self._load_tab(0)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Load content when the user switches tabs."""
        if event.pane and event.pane.id in _TAB_ID_TO_INDEX:
            self._load_tab(_TAB_ID_TO_INDEX[event.pane.id])

    def _load_tab(self, tab_index: int) -> None:
        """Trigger a background load for the given tab if not already loaded."""
        if tab_index in self._loaded_tabs:
            return
        self._loaded_tabs.add(tab_index)
        self._fetch_and_update(tab_index)

    @work(thread=True)
    def _fetch_and_update(self, tab_index: int) -> None:
        """Fetch tab content in a background thread and update the label."""
        task_id = self._task_data.get("id") or ""
        content = _fetch_tab_content(task_id, tab_index)
        widget_id = _TAB_CONTENT_IDS[tab_index]

        def update() -> None:
            try:
                label = self.query_one(f"#{widget_id}", Label)
                label.update(content)
            except Exception:
                pass

        self.app.call_from_thread(update)
