"""Drafts tab — server-sourced draft ideas, status filters, master-detail view."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Button, Label, ListItem, ListView
from textual.containers import Horizontal, Vertical, VerticalScroll

from .done import _format_age


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

    def __init__(self, draft: dict, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._draft = draft

    @property
    def draft_data(self) -> dict:
        return self._draft

    def compose(self) -> ComposeResult:
        status = self._draft.get("status", "idea")
        tag, color = _STATUS_TAGS.get(status, ("???", "#e0e0e0"))
        title = self._draft.get("title", "Untitled")
        label_text = Text()
        label_text.append(f" {tag} ", style=f"bold {color}")
        label_text.append(f" {title}", style="#e0e0e0")
        yield Label(label_text, classes="draft-list-label")


class DraftsTab(Widget):
    """Master-detail drafts view: filter buttons + list on left, file content on right."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    DEFAULT_CSS = """
    DraftsTab {
        height: 100%;
    }
    """

    def __init__(self, report: dict | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._drafts: list[dict] = []
        self._filters: dict[str, bool] = dict(_DEFAULT_FILTERS)

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
                yield Label(
                    "No draft selected.",
                    id="draft-content",
                    classes="dim-text",
                )

    def on_mount(self) -> None:
        self._refresh_list()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Toggle a filter button on/off."""
        btn_id = event.button.id or ""
        if not btn_id.startswith("filter-"):
            return
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
            for draft in filtered:
                lv.append(_DraftItem(draft))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Update the content pane when a draft is selected."""
        if not isinstance(event.item, _DraftItem):
            return
        draft = event.item.draft_data
        content = _load_draft_content(draft)
        try:
            label = self.query_one("#draft-content", Label)
            label.update(content or "(empty)")
        except Exception:
            pass

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
        self._refresh_list()
