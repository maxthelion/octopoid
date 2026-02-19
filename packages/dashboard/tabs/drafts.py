"""Drafts tab — project management draft ideas, master-detail view."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView
from textual.containers import Horizontal, Vertical, VerticalScroll


def _load_drafts() -> list[dict]:
    """Load draft files from project-management/drafts/."""
    drafts_dir = Path.cwd() / "project-management" / "drafts"
    if not drafts_dir.exists():
        return []
    result = []
    for f in sorted(drafts_dir.glob("*.md")):
        title = f.stem.replace("-", " ").title()
        try:
            first_line = f.read_text().split("\n", 1)[0]
            if first_line.startswith("# "):
                title = first_line[2:].strip()
        except OSError:
            pass
        result.append({"filename": f.name, "title": title, "path": str(f)})
    return result


def _load_draft_content(draft: dict) -> str:
    """Load the full text content of a draft file."""
    path_str = draft.get("path") or ""
    if not path_str:
        return ""
    try:
        return Path(path_str).read_text()
    except OSError:
        return "(could not read file)"


class _DraftItem(ListItem):
    """A single draft entry in the left list."""

    def __init__(self, index: int, draft: dict, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._index = index
        self._draft = draft

    @property
    def draft_data(self) -> dict:
        return self._draft

    def compose(self) -> ComposeResult:
        title = self._draft.get("title", self._draft.get("filename", "?"))
        yield Label(f"{self._index + 1}. {title}", classes="draft-list-label")


class DraftsTab(Widget):
    """Master-detail drafts view: list on left, file content on right."""

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
        # report is not used directly — drafts come from the filesystem
        self._drafts: list[dict] = []

    def compose(self) -> ComposeResult:
        self._drafts = _load_drafts()
        selected = self._drafts[0] if self._drafts else None

        with Horizontal(classes="drafts-layout"):
            with Vertical(classes="draft-list-panel", id="draft-list-panel"):
                yield Label(" DRAFTS ", classes="section-header")
                with ListView(id="draft-listview", classes="draft-listview"):
                    if not self._drafts:
                        yield ListItem(Label("No drafts found.", classes="dim-text"))
                    else:
                        for i, draft in enumerate(self._drafts):
                            yield _DraftItem(i, draft)

            with VerticalScroll(id="draft-content-panel", classes="draft-content-panel"):
                yield Label(" CONTENT ", classes="section-header")
                if selected:
                    content = _load_draft_content(selected)
                    yield Label(
                        content or "(empty)",
                        id="draft-content",
                        classes="draft-content-text",
                    )
                else:
                    yield Label(
                        "No draft selected.",
                        id="draft-content",
                        classes="dim-text",
                    )

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
        """Rescan the drafts directory and recompose."""
        self.refresh(recompose=True)
