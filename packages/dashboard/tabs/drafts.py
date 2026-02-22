"""Drafts tab — server-sourced draft ideas, status filters, master-detail view.

Two nested sub-tabs split drafts by author:
  - User Drafts: author is None/empty/human
  - Agent Drafts: author is agent
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Input, Label, ListItem, ListView, Markdown, TabbedContent, TabPane
from textual.containers import Horizontal, Vertical, VerticalScroll

from .base import TabBase


# Status tag labels and colors
_STATUS_TAGS: dict[str, tuple[str, str]] = {
    "active": ("ACT", "#66bb6a"),
    "idea": ("IDEA", "#4fc3f7"),
    "partial": ("PART", "#ffa726"),
    "complete": ("DONE", "#616161"),
    "superseded": ("ARCH", "#ef5350"),
}

# Filter labels shown on the buttons
_FILTER_LABELS: list[tuple[str, str]] = [
    ("active", "Active"),
    ("idea", "Idea"),
    ("partial", "Partial"),
    ("complete", "Complete"),
    ("superseded", "Archived"),
]

# Default filter state: Archived (superseded) is hidden by default
_DEFAULT_FILTERS: dict[str, bool] = {
    "active": True,
    "idea": True,
    "partial": True,
    "complete": True,
    "superseded": False,
}


def _load_draft_content(draft: dict) -> str:
    """Load the full text content of a draft file from its file_path field."""
    file_path = draft.get("file_path") or ""
    if not file_path:
        return ""
    try:
        return Path(file_path).read_text()
    except OSError:
        return "(could not read file)"


def _post_inbox_message(draft_id: int | str, message: str) -> None:
    """Post a draft action command message via sdk.messages.create()."""
    try:
        from orchestrator.sdk import get_sdk
        sdk = get_sdk()
        sdk.messages.create(
            task_id=str(draft_id),
            from_actor="human",
            type="action_command",
            content=message,
            to_actor="agent",
        )
    except Exception:
        pass


def _action_message(action: dict, draft_id: int | str) -> str:
    """Build the inbox message text for a draft action button click."""
    action_type = action.get("action_type", "")
    label = action.get("label", "")
    if action_type == "enqueue_draft":
        return f"enqueue the work for draft {draft_id}"
    elif action_type == "process_draft":
        return f"process draft {draft_id}"
    elif action_type == "archive_draft":
        return f"archive draft {draft_id} as superseded"
    else:
        return f"{label} for draft {draft_id}"


class _DraftItem(ListItem):
    """A single draft entry in the left list — compact 1-line format."""

    def __init__(self, draft: dict, num: int = 0, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._draft = draft
        self._num = num

    @property
    def draft_data(self) -> dict:
        return self._draft

    def compose(self) -> ComposeResult:
        status = self._draft.get("status", "idea")
        tag, color = _STATUS_TAGS.get(status, ("???", "#e0e0e0"))
        title = self._draft.get("title", "Untitled")
        label_text = Text()
        label_text.append(f"{self._num:>3} ", style="bold #616161")
        label_text.append(f"{tag} ", style=f"bold {color}")
        label_text.append(title, style="#e0e0e0")
        yield Label(label_text, classes="draft-list-label")


class DraftsTab(TabBase):
    """Master-detail drafts view: nested User/Agent sub-tabs on left, content on right.

    The right pane shows the draft content in a scrollable area, with a dynamic
    action bar at the bottom. Buttons are rendered from action_data["buttons"]
    when available, or fall back to a single button per action using the action
    label. An Other... free-text input posts a custom message to the inbox.
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, report: dict | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._drafts: list[dict] = []
        self._user_filters: dict[str, bool] = dict(_DEFAULT_FILTERS)
        self._agent_filters: dict[str, bool] = dict(_DEFAULT_FILTERS)
        self._selected_draft: dict | None = None
        self._selected_draft_id: int | str | None = None
        # Maps button ID -> {action_id, entity_type, entity_id, command}
        self._btn_context: dict[str, dict] = {}

    @property
    def _user_drafts(self) -> list[dict]:
        """Drafts authored by humans (author is None, empty, or 'human')."""
        return [d for d in self._drafts if d.get("author") != "agent"]

    @property
    def _agent_drafts(self) -> list[dict]:
        """Drafts authored by agents."""
        return [d for d in self._drafts if d.get("author") == "agent"]

    def compose(self) -> ComposeResult:
        with Horizontal(classes="drafts-layout"):
            with Vertical(classes="draft-list-panel", id="draft-list-panel"):
                with TabbedContent(id="draft-subtabs"):
                    with TabPane("User Drafts", id="user-drafts-pane"):
                        with Horizontal(classes="draft-filters", id="user-draft-filters"):
                            for status, label in _FILTER_LABELS:
                                active_class = " draft-filter-active" if self._user_filters[status] else ""
                                yield Button(
                                    label,
                                    id=f"user-filter-{status}",
                                    classes=f"draft-filter-btn draft-filter-{status}{active_class}",
                                )
                        with ListView(id="user-draft-listview", classes="draft-listview"):
                            pass
                    with TabPane("Agent Drafts", id="agent-drafts-pane"):
                        with Horizontal(classes="draft-filters", id="agent-draft-filters"):
                            for status, label in _FILTER_LABELS:
                                active_class = " draft-filter-active" if self._agent_filters[status] else ""
                                yield Button(
                                    label,
                                    id=f"agent-filter-{status}",
                                    classes=f"draft-filter-btn draft-filter-{status}{active_class}",
                                )
                        with ListView(id="agent-draft-listview", classes="draft-listview"):
                            pass

            with Vertical(id="draft-content-panel", classes="draft-content-panel"):
                with VerticalScroll(id="draft-content-scroll", classes="draft-content-scroll"):
                    yield Label(" CONTENT ", classes="section-header")
                    yield Markdown(
                        "_No draft selected._",
                        id="draft-content",
                        classes="draft-content-text",
                    )
                # Action bar at the bottom — buttons mounted dynamically on selection
                with Horizontal(id="draft-action-bar", classes="draft-action-bar"):
                    yield Input(
                        placeholder="Other...",
                        id="draft-action-other",
                        classes="draft-action-input",
                    )

    def on_mount(self) -> None:
        self._refresh_all_lists()

    def _get_active_listview_id(self) -> str:
        """Return the ID of the currently active list view based on selected sub-tab."""
        try:
            tabs = self.query_one("#draft-subtabs", TabbedContent)
            if tabs.active == "agent-drafts-pane":
                return "agent-draft-listview"
        except Exception:
            pass
        return "user-draft-listview"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle filter toggle buttons and draft action buttons."""
        btn_id = event.button.id or ""

        if btn_id.startswith("user-filter-"):
            status = btn_id[len("user-filter-"):]
            if status not in self._user_filters:
                return
            self._user_filters[status] = not self._user_filters[status]
            btn = event.button
            if self._user_filters[status]:
                btn.add_class("draft-filter-active")
            else:
                btn.remove_class("draft-filter-active")
            self._refresh_user_list()
            return

        if btn_id.startswith("agent-filter-"):
            status = btn_id[len("agent-filter-"):]
            if status not in self._agent_filters:
                return
            self._agent_filters[status] = not self._agent_filters[status]
            btn = event.button
            if self._agent_filters[status]:
                btn.add_class("draft-filter-active")
            else:
                btn.remove_class("draft-filter-active")
            self._refresh_agent_list()
            return

        if btn_id in self._btn_context:
            ctx = self._btn_context[btn_id]
            draft_id = ctx["entity_id"]
            command = ctx["command"]
            _post_inbox_message(draft_id, command)
            self.app.notify(f"Sent: {command}", timeout=3)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Post a free-text Other... message to the inbox."""
        if event.input.id != "draft-action-other":
            return
        text = event.value.strip()
        if not text:
            return
        if self._selected_draft is None:
            self.app.notify("No draft selected", severity="warning", timeout=3)
            return
        draft_id = self._selected_draft.get("id", "")
        message = f"[Draft {draft_id}] {text}"
        _post_inbox_message(draft_id, message)
        self.app.notify(f"Sent: {message}", timeout=3)
        event.input.value = ""

    def _refresh_user_list(self) -> None:
        """Repopulate the user draft list with currently filtered drafts."""
        try:
            lv = self.query_one("#user-draft-listview", ListView)
        except Exception:
            return
        lv.clear()
        filtered = [
            d for d in self._user_drafts
            if self._user_filters.get(d.get("status", "idea"), True)
        ]
        if not filtered:
            lv.append(ListItem(Label("No drafts match filters.", classes="dim-text")))
        else:
            for idx, draft in enumerate(filtered, start=1):
                lv.append(_DraftItem(draft, num=draft.get("id", idx)))

    def _refresh_agent_list(self) -> None:
        """Repopulate the agent draft list with currently filtered drafts."""
        try:
            lv = self.query_one("#agent-draft-listview", ListView)
        except Exception:
            return
        lv.clear()
        filtered = [
            d for d in self._agent_drafts
            if self._agent_filters.get(d.get("status", "idea"), True)
        ]
        if not filtered:
            lv.append(ListItem(Label("No drafts match filters.", classes="dim-text")))
        else:
            for idx, draft in enumerate(filtered, start=1):
                lv.append(_DraftItem(draft, num=draft.get("id", idx)))

    def _refresh_all_lists(self) -> None:
        """Refresh both user and agent draft lists."""
        self._refresh_user_list()
        self._refresh_agent_list()

    def _update_action_bar(self, draft: dict) -> None:
        """Rebuild the action bar buttons for the selected draft.

        For actions with action_data["buttons"], renders one Button per button
        definition. For actions without action_data, renders a single button
        using the action label.
        """
        try:
            bar = self.query_one("#draft-action-bar", Horizontal)
        except Exception:
            return

        # Remove all existing action buttons (leave the Input in place)
        for btn in list(bar.query(Button)):
            btn.remove()
        self._btn_context.clear()

        draft_id = str(draft.get("id", ""))
        actions = draft.get("actions", [])

        for action in actions:
            action_id = action.get("id")
            if not action_id:
                continue

            action_data = action.get("action_data") or {}
            if isinstance(action_data, str):
                try:
                    action_data = json.loads(action_data)
                except (json.JSONDecodeError, TypeError):
                    action_data = {}

            buttons = action_data.get("buttons", [])
            if buttons:
                # action_data-driven: render one button per definition
                for idx, btn_def in enumerate(buttons):
                    btn_id = f"draft-btn-{action_id}-{idx}"
                    label = btn_def.get("label") or "Action"
                    command = btn_def.get("command", "")
                    self._btn_context[btn_id] = {
                        "action_id": action_id,
                        "entity_type": "draft",
                        "entity_id": draft_id,
                        "command": command,
                    }
                    try:
                        inp = bar.query_one(Input)
                        bar.mount(Button(label, id=btn_id, classes="draft-action-btn"), before=inp)
                    except Exception:
                        bar.mount(Button(label, id=btn_id, classes="draft-action-btn"))
            else:
                # Default fallback: single button per action using action label
                btn_id = f"draft-btn-{action_id}-0"
                label = action.get("label") or action.get("action_type") or "Action"
                command = _action_message(action, draft_id)
                self._btn_context[btn_id] = {
                    "action_id": action_id,
                    "entity_type": "draft",
                    "entity_id": draft_id,
                    "command": command,
                }
                try:
                    inp = bar.query_one(Input)
                    bar.mount(Button(label, id=btn_id, classes="draft-action-btn"), before=inp)
                except Exception:
                    bar.mount(Button(label, id=btn_id, classes="draft-action-btn"))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Update the content pane and action bar when a draft is selected."""
        if not isinstance(event.item, _DraftItem):
            return
        draft = event.item.draft_data
        draft_id = draft.get("id")

        # Skip re-render if the same draft is already selected
        if draft_id is not None and draft_id == self._selected_draft_id:
            return

        self._selected_draft = draft
        self._selected_draft_id = draft_id

        # Update content pane
        content = _load_draft_content(draft)
        try:
            md = self.query_one("#draft-content", Markdown)
            md.update(content or "_empty_")
        except Exception:
            pass

        # Update action bar buttons
        self._update_action_bar(draft)

        # If the draft has no file_path yet (created in two steps: register then
        # write file + PATCH), trigger a background refresh.  When the fetch
        # completes, update_data() will re-render the content once file_path is set.
        if not (draft.get("file_path") or ""):
            try:
                self.app.action_refresh()
            except Exception:
                pass

    def action_cursor_down(self) -> None:
        lv_id = self._get_active_listview_id()
        try:
            self.query_one(f"#{lv_id}", ListView).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        lv_id = self._get_active_listview_id()
        try:
            self.query_one(f"#{lv_id}", ListView).action_cursor_up()
        except Exception:
            pass

    def update_data(self, report: dict) -> None:
        """Update drafts from report and refresh the lists."""
        self._drafts = report.get("drafts", [])
        # Keep the selected draft's actions up-to-date if one is selected
        if self._selected_draft is not None:
            selected_id = self._selected_draft.get("id")
            for d in self._drafts:
                if d.get("id") == selected_id:
                    old_file_path = self._selected_draft.get("file_path") or ""
                    self._selected_draft = d
                    self._update_action_bar(d)
                    # Re-render content if file_path is newly available (e.g. draft
                    # was created with file_path=None and later PATCH-ed with the path).
                    new_file_path = d.get("file_path") or ""
                    if new_file_path and not old_file_path:
                        content = _load_draft_content(d)
                        try:
                            md = self.query_one("#draft-content", Markdown)
                            md.update(content or "_empty_")
                        except Exception:
                            pass
                    break
        self._refresh_all_lists()
