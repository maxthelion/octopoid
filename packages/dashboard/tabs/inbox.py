"""Inbox tab — master-detail view of server messages sent to human.

Messages are fetched via sdk.messages.list(to_actor="human") and displayed
in a master-detail layout: list on the left (newest first), full content on
the right as Markdown.

Messages with embedded "actions" in their content JSON show action buttons in
a fixed bar at the bottom of the content pane. Clicking a button posts an
action_command message back to the sender via sdk.messages.create().
"""

from __future__ import annotations

import json

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Label, ListItem, ListView, Markdown
from textual.containers import Horizontal, Vertical, VerticalScroll

from .base import TabBase


# Message type → (short tag, color)
_TYPE_TAGS: dict[str, tuple[str, str]] = {
    "action_proposal": ("PROP", "#4fc3f7"),
    "action_command": ("CMD", "#66bb6a"),
    "worker_result": ("RESULT", "#ce93d8"),
    "info": ("INFO", "#4fc3f7"),
    "warning": ("WARN", "#ffa726"),
    "error": ("ERR", "#ef5350"),
    "question": ("?", "#ffa726"),
}

# Hotkey labels for action button slots
_ACTION_HOTKEYS = ["A", "B", "C"]

# Max action button slots
_NUM_ACTION_SLOTS = 3


def _parse_content(raw: str) -> tuple[str, list[dict]]:
    """Parse message content into (markdown_text, actions).

    If content is JSON, render the human-readable fields as markdown
    and extract any embedded actions list. Otherwise treat as plain text.

    Returns:
        (markdown_body, actions) — actions is a list of dicts with 'label'
        and 'action_type' keys (or empty list if none).
    """
    if not raw:
        return "_empty_", []

    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return raw, []

        actions = data.get("actions", [])

        # Build markdown from known fields
        lines: list[str] = []
        description = data.get("description") or data.get("body") or data.get("message")
        if description:
            lines.append(description)
            lines.append("")

        # Render remaining metadata fields
        skip = {"description", "body", "message", "actions"}
        meta = {k: v for k, v in data.items() if k not in skip and v is not None}
        if meta:
            if lines:
                lines.append("---")
                lines.append("")
            for k, v in meta.items():
                lines.append(f"**{k}:** {v}")

        body = "\n".join(lines) if lines else raw
        return body, actions

    except (json.JSONDecodeError, ValueError):
        return raw, []


def _content_preview(raw: str, max_len: int = 60) -> str:
    """Extract a short preview from raw content for the list item."""
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            preview = (
                data.get("description")
                or data.get("body")
                or data.get("message")
                or ""
            )
            if not preview:
                # Use first non-action, non-empty string value
                for v in data.values():
                    if isinstance(v, str) and v:
                        preview = v
                        break
            raw = preview or raw
    except (json.JSONDecodeError, ValueError):
        pass

    raw = raw.strip().replace("\n", " ")
    if len(raw) > max_len:
        return raw[:max_len] + "…"
    return raw


class _MessageItem(ListItem):
    """A single inbox message entry in the list — compact 1-line format."""

    def __init__(self, message: dict, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._message = message

    @property
    def message_data(self) -> dict:
        return self._message

    def compose(self) -> ComposeResult:
        msg_type = self._message.get("type", "")
        from_actor = self._message.get("from_actor", "?")
        raw_content = self._message.get("content", "")

        tag, color = _TYPE_TAGS.get(msg_type, ("MSG", "#e0e0e0"))
        preview = _content_preview(raw_content)

        label_text = Text()
        label_text.append(f"{tag} ", style=f"bold {color}")
        label_text.append(f"{from_actor}: ", style="bold #e0e0e0")
        label_text.append(preview, style="#616161")
        yield Label(label_text, classes="inbox-message-label")


class InboxTab(TabBase):
    """Master-detail inbox view: message list on left, content on right.

    Messages are fetched from report["messages"] (populated via SDK) and
    displayed newest-first. Selecting a message shows full content as Markdown.
    Messages with embedded actions show buttons in a fixed action bar; clicking
    posts an action_command back via sdk.messages.create().
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, report: dict | None = None, **kwargs: object) -> None:
        super().__init__(report=report, **kwargs)
        self._messages: list[dict] = []
        self._selected_message: dict | None = None
        self._selected_message_id: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="inbox-layout"):
            with Vertical(classes="inbox-list-panel", id="inbox-list-panel"):
                yield Label(" INBOX ", classes="section-header")
                with ListView(id="inbox-listview", classes="inbox-listview"):
                    pass

            with Vertical(id="inbox-content-panel", classes="inbox-content-panel"):
                with VerticalScroll(id="inbox-content-scroll", classes="inbox-content-scroll"):
                    yield Label(" MESSAGE ", classes="section-header")
                    yield Markdown(
                        "_No message selected._",
                        id="inbox-content",
                        classes="inbox-content-text",
                    )
                with Horizontal(id="inbox-action-bar", classes="inbox-action-bar"):
                    for i in range(_NUM_ACTION_SLOTS):
                        yield Button(
                            "",
                            id=f"inbox-action-{i}",
                            classes="inbox-action-btn",
                            disabled=True,
                        )

    def on_mount(self) -> None:
        for i in range(_NUM_ACTION_SLOTS):
            try:
                self.query_one(f"#inbox-action-{i}", Button).display = False
            except Exception:
                pass
        self._refresh_list()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if not btn_id.startswith("inbox-action-"):
            return

        idx_str = btn_id[len("inbox-action-"):]
        try:
            idx = int(idx_str)
        except ValueError:
            return

        if self._selected_message is None:
            return

        _, actions = _parse_content(self._selected_message.get("content", ""))
        if not (0 <= idx < len(actions)):
            return

        action = actions[idx]
        self._post_action_command(action)

    def _post_action_command(self, action: dict) -> None:
        """Post an action_command message back to the sender."""
        if self._selected_message is None:
            return

        action_type = action.get("action_type", "")
        label = action.get("label", "")
        task_id = self._selected_message.get("task_id", "")
        from_actor = self._selected_message.get("from_actor", "agent")
        source_id = self._selected_message.get("id", "")

        content = json.dumps({
            "action_type": action_type,
            "label": label,
            "source_message_id": source_id,
        })

        try:
            from orchestrator.sdk import get_sdk
            sdk = get_sdk()
            sdk.messages.create(
                task_id=task_id or "dashboard",
                from_actor="human",
                type="action_command",
                content=content,
                to_actor=from_actor,
            )
            self.app.notify(f"Sent: [{label}] → {from_actor}", timeout=3)
        except Exception as exc:
            self.app.notify(f"Failed to send: {exc}", severity="error", timeout=4)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, _MessageItem):
            return

        message = event.item.message_data
        msg_id = message.get("id")

        if msg_id is not None and msg_id == self._selected_message_id:
            return

        self._selected_message = message
        self._selected_message_id = msg_id

        # Render content
        raw_content = message.get("content", "")
        body, actions = _parse_content(raw_content)

        # Add metadata header
        msg_type = message.get("type", "")
        from_actor = message.get("from_actor", "")
        created_at = message.get("created_at", "")
        header_lines = [
            f"**From:** {from_actor}  **Type:** {msg_type}  **Time:** {created_at}",
            "",
            "---",
            "",
        ]
        full_body = "\n".join(header_lines) + body

        try:
            md = self.query_one("#inbox-content", Markdown)
            md.update(full_body)
        except Exception:
            pass

        self._update_action_bar(actions)

    def _update_action_bar(self, actions: list[dict]) -> None:
        """Refresh the action bar buttons to match the selected message's actions."""
        for i in range(_NUM_ACTION_SLOTS):
            try:
                btn = self.query_one(f"#inbox-action-{i}", Button)
            except Exception:
                continue
            if i < len(actions):
                action = actions[i]
                hotkey = _ACTION_HOTKEYS[i]
                label = action.get("label", "")
                btn.label = f"[{hotkey}] {label}"
                btn.disabled = False
                btn.display = True
            else:
                btn.label = ""
                btn.disabled = True
                btn.display = False

    def _refresh_list(self) -> None:
        """Repopulate the message list."""
        try:
            lv = self.query_one("#inbox-listview", ListView)
        except Exception:
            return
        lv.clear()
        if not self._messages:
            lv.append(ListItem(Label("No messages.", classes="dim-text")))
        else:
            for message in self._messages:
                lv.append(_MessageItem(message))

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#inbox-listview", ListView).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#inbox-listview", ListView).action_cursor_up()
        except Exception:
            pass

    def update_data(self, report: dict) -> None:
        """Update messages from report and refresh the list."""
        self._messages = report.get("messages", [])
        # Keep selected message up-to-date
        if self._selected_message is not None:
            selected_id = self._selected_message.get("id")
            for m in self._messages:
                if m.get("id") == selected_id:
                    self._selected_message = m
                    _, actions = _parse_content(m.get("content", ""))
                    self._update_action_bar(actions)
                    break
        self._refresh_list()
