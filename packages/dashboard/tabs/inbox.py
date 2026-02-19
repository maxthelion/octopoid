"""Inbox tab — Proposals, Messages, and Drafts columns."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView
from textual.containers import Horizontal, Vertical


class _InboxItem(ListItem):
    """A single item in an inbox column."""

    def __init__(self, text: str, css_class: str = "", **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._text = text
        self._item_css_class = css_class

    def compose(self) -> ComposeResult:
        yield Label(f"• {self._text}", classes=f"inbox-item-label {self._item_css_class}")


class _InboxColumn(Widget):
    """A single column in the inbox: header + scrollable list of items."""

    DEFAULT_CSS = """
    _InboxColumn {
        width: 1fr;
        height: 100%;
        border-right: solid $panel-darken-2;
    }
    _InboxColumn:last-of-type {
        border-right: none;
    }
    """

    def __init__(
        self,
        title: str,
        items: list[dict],
        item_type: str = "generic",
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._col_title = title
        self._items = items
        self._item_type = item_type

    def compose(self) -> ComposeResult:
        count = len(self._items)
        yield Label(f" {self._col_title} ({count}) ", classes="section-header")
        with ListView(classes="inbox-list"):
            if not self._items:
                yield _InboxItem("No pending items", css_class="dim-text")
            else:
                for item in self._items:
                    text, css_class = self._format_item(item)
                    yield _InboxItem(text, css_class=css_class)

    def _format_item(self, item: dict) -> tuple[str, str]:
        """Return (display_text, css_class) for an item."""
        if self._item_type == "proposal":
            title = item.get("title", "untitled")
            return title, ""
        elif self._item_type == "message":
            mtype = item.get("type", "info")
            fname = item.get("filename", item.get("message", ""))
            css_class = (
                "message--error" if mtype == "error"
                else "message--warning" if mtype == "warning"
                else ""
            )
            return f"[{mtype}] {fname}", css_class
        else:
            title = item.get("title", item.get("filename", "untitled"))
            return title, ""


class InboxTab(Widget):
    """Three-column inbox: Proposals | Messages | Drafts."""

    DEFAULT_CSS = """
    InboxTab {
        height: 100%;
    }
    """

    def __init__(self, report: dict | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._report = report or {}

    def compose(self) -> ComposeResult:
        proposals = self._report.get("proposals", [])
        messages = self._report.get("messages", [])
        drafts = self._report.get("drafts", [])

        with Horizontal(classes="inbox-columns"):
            yield _InboxColumn(
                "PROPOSALS",
                proposals,
                item_type="proposal",
                classes="inbox-column",
                id="inbox-proposals",
            )
            yield _InboxColumn(
                "MESSAGES",
                messages,
                item_type="message",
                classes="inbox-column",
                id="inbox-messages",
            )
            yield _InboxColumn(
                "DRAFTS",
                drafts,
                item_type="draft",
                classes="inbox-column",
                id="inbox-drafts",
            )

    def update_data(self, report: dict) -> None:
        """Replace the report and recompose the inbox columns."""
        self._report = report
        self.refresh(recompose=True)
