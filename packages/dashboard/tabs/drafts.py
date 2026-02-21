"""Drafts tab — server-sourced draft ideas, status filters, master-detail view."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Label, ListItem, ListView, Markdown
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
    """Master-detail drafts view: filter buttons + list on left, file content on right."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, report: dict | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._drafts: list[dict] = []
        self._filters: dict[str, bool] = dict(_DEFAULT_FILTERS)
        self._selected_draft: dict | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="drafts-layout"):
            with Vertical(classes="draft-list-panel", id="draft-list-panel"):
                yield Label(" DRAFTS ", classes="section-header")
                with Horizontal(classes="draft-filters", id="draft-filters"):
                    for status, label in _FILTER_LABELS:
                        active_class = " draft-filter-active" if self._filters[status] else ""
                        yield Button(
                            label,
                            id=f"filter-{status}",
                            classes=f"draft-filter-btn draft-filter-{status}{active_class}",
                        )
                with ListView(id="draft-listview", classes="draft-listview"):
                    pass

            with VerticalScroll(id="draft-content-panel", classes="draft-content-panel"):
                yield Label(" CONTENT ", classes="section-header")
                yield Horizontal(id="draft-action-bar", classes="draft-action-bar")
                yield Markdown(
                    "_No draft selected._",
                    id="draft-content",
                    classes="draft-content-text",
                )

    def on_mount(self) -> None:
        self._refresh_list()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle filter toggle and action button presses."""
        btn_id = event.button.id or ""

        if btn_id.startswith("filter-"):
            status = btn_id[len("filter-"):]
            if status not in self._filters:
                return
            self._filters[status] = not self._filters[status]
            btn = event.button
            if self._filters[status]:
                btn.add_class("draft-filter-active")
            else:
                btn.remove_class("draft-filter-active")
            self._refresh_list()
            return

        if btn_id.startswith("action-"):
            action_id = btn_id[len("action-"):]
            self._execute_action(action_id)

    @work(thread=True)
    def _execute_action(self, action_id: str) -> None:
        """Execute a draft action in a background thread."""
        try:
            from orchestrator.sdk import get_sdk
            sdk = get_sdk()
            sdk.actions.execute(action_id)
            self.app.call_from_thread(
                self.app.notify,
                "Action requested",
                timeout=3,
            )
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify,
                f"Action failed: {exc}",
                severity="error",
                timeout=4,
            )

    def _refresh_list(self) -> None:
        """Repopulate the list with currently filtered drafts."""
        try:
            lv = self.query_one("#draft-listview", ListView)
        except Exception:
            return
        lv.clear()
        filtered = [
            d for d in self._drafts
            if self._filters.get(d.get("status", "idea"), True)
        ]
        if not filtered:
            lv.append(ListItem(Label("No drafts match filters.", classes="dim-text")))
        else:
            for idx, draft in enumerate(filtered, start=1):
                lv.append(_DraftItem(draft, num=draft.get("id", idx)))

    def _refresh_action_bar(self) -> None:
        """Rebuild the action bar for the currently selected draft."""
        try:
            bar = self.query_one("#draft-action-bar", Horizontal)
        except Exception:
            return

        bar.remove_children()

        if not self._selected_draft:
            return

        actions = self._selected_draft.get("actions") or []
        for action in actions:
            action_id = action.get("id")
            label = action.get("label") or action.get("action_type") or "Action"
            if not action_id:
                continue
            bar.mount(
                Button(
                    label,
                    id=f"action-{action_id}",
                    classes="draft-action-btn",
                )
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Update the content pane when a draft is selected."""
        if not isinstance(event.item, _DraftItem):
            return
        draft = event.item.draft_data
        self._selected_draft = draft
        content = _load_draft_content(draft)
        try:
            md = self.query_one("#draft-content", Markdown)
            md.update(content or "_empty_")
        except Exception:
            pass
        self._refresh_action_bar()

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#draft-listview", ListView).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#draft-listview", ListView).action_cursor_up()
        except Exception:
            pass

    def update_data(self, report: dict) -> None:
        """Update drafts from report and refresh the list."""
        self._drafts = report.get("drafts", [])
        # Keep the selected draft's actions up-to-date if one is selected
        if self._selected_draft is not None:
            selected_id = self._selected_draft.get("id")
            for d in self._drafts:
                if d.get("id") == selected_id:
                    self._selected_draft = d
                    self._refresh_action_bar()
                    break
        self._refresh_list()
