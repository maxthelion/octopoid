"""Inbox tab — server-sourced messages addressed to the human, master-detail view.

Left panel: scrollable message list (newest first) showing type, from_actor, and
content preview. Right panel: full message content rendered as Markdown. Fixed
action bar at bottom: buttons parsed from content JSON, plus free-text input.

Clicking an action button posts an action_command back via sdk.messages.create().
"""

from __future__ import annotations

import json

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Input, Label, ListItem, ListView, Markdown
from textual.containers import Horizontal, Vertical, VerticalScroll

from .base import TabBase


# Message type display tags and colors
_TYPE_TAGS: dict[str, tuple[str, str]] = {
    "action_proposal": ("PROP", "#ce93d8"),
    "action_command": ("CMD", "#4fc3f7"),
    "worker_result": ("RSLT", "#66bb6a"),
    "info": ("INFO", "#4fc3f7"),
    "warning": ("WARN", "#ffa726"),
    "error": ("ERR", "#ef5350"),
}

# Number of fixed action button slots
_NUM_ACTION_SLOTS = 3
_ACTION_HOTKEYS = ["A", "B", "C"]


def _parse_content(content: str) -> tuple[str, list[dict]]:
    """Parse a message's content field.

    Returns:
        (body_markdown, actions) where body_markdown is the text to display
        and actions is a list of action dicts (may be empty).
    """
    if not content:
        return "_empty_", []

    try:
        data = json.loads(content)
        if isinstance(data, dict):
            body = data.get("body") or data.get("text") or data.get("message") or ""
            if not body:
                # Fall back to pretty-printing the JSON minus the actions key
                display_data = {k: v for k, v in data.items() if k != "actions"}
                body = f"```json\n{json.dumps(display_data, indent=2)}\n```" if display_data else content
            actions = data.get("actions", [])
            if not isinstance(actions, list):
                actions = []
            return body or "_empty_", actions
    except (json.JSONDecodeError, ValueError):
        pass

    # Plain text content
    return content, []


def _content_preview(content: str, max_len: int = 60) -> str:
    """Return a short preview of message content for list display."""
    body, _ = _parse_content(content)
    # Strip markdown formatting for preview
    preview = body.replace("\n", " ").replace("_", "").replace("*", "").replace("`", "")
    if len(preview) > max_len:
        return preview[:max_len] + "…"
    return preview


def _post_action_command(task_id: str, content: str) -> None:
    """Post an action_command reply via sdk.messages.create()."""
    try:
        from orchestrator.sdk import get_sdk
        sdk = get_sdk()
        sdk.messages.create(
            task_id=task_id,
            from_actor="human",
            type="action_command",
            content=content,
            to_actor="agent",
        )
    except Exception:
        pass


class _MessageItem(ListItem):
    """A single message entry in the left list — compact 1-line format."""

    def __init__(self, message: dict, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._message = message

    @property
    def message_data(self) -> dict:
        return self._message

    def compose(self) -> ComposeResult:
        msg_type = self._message.get("type", "")
        from_actor = self._message.get("from_actor", "")
        content = self._message.get("content", "")
        tag, color = _TYPE_TAGS.get(msg_type, ("MSG", "#e0e0e0"))
        preview = _content_preview(content)

        label_text = Text()
        label_text.append(f"{tag} ", style=f"bold {color}")
        label_text.append(f"{from_actor}: ", style="bold #616161")
        label_text.append(preview, style="#e0e0e0")
        yield Label(label_text, classes="inbox-msg-label")


class InboxTab(TabBase):
    """Master-detail inbox view: message list on left, content on right.

    Messages are sourced from sdk.messages.list(to_actor='human') via the
    report dict's 'messages' key. Ordered newest first.

    The right pane shows content as Markdown with a fixed action bar at
    the bottom. Action buttons are parsed from the message content JSON.
    Clicking one posts an action_command back via sdk.messages.create().
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, report: dict | None = None, **kwargs: object) -> None:
        super().__init__(report=report, **kwargs)
        self._messages: list[dict] = []
        self._selected_message: dict | None = None
        self._selected_message_id: str | int | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="inbox-layout"):
            with Vertical(classes="inbox-list-panel", id="inbox-list-panel"):
                yield Label(" INBOX ", classes="section-header")
                with ListView(id="inbox-listview", classes="inbox-msg-listview"):
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
                    yield Input(
                        placeholder="Other...",
                        id="inbox-action-other",
                        classes="inbox-action-input",
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
        if 0 <= idx < len(actions):
            action = actions[idx]
            action_label = action.get("label", "")
            action_type = action.get("action_type", action.get("type", ""))
            task_id = self._selected_message.get("task_id") or ""
            cmd_content = json.dumps({"action_type": action_type, "label": action_label, **{
                k: v for k, v in action.items() if k not in ("label", "action_type")
            }})
            if task_id:
                _post_action_command(task_id, cmd_content)
            self.app.notify(f"Sent: {action_label}", timeout=3)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "inbox-action-other":
            return
        text = event.value.strip()
        if not text:
            return
        if self._selected_message is None:
            self.app.notify("No message selected", severity="warning", timeout=3)
            return
        task_id = self._selected_message.get("task_id") or ""
        if task_id:
            _post_action_command(task_id, text)
        self.app.notify(f"Sent: {text}", timeout=3)
        event.input.value = ""

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, _MessageItem):
            return
        message = event.item.message_data
        msg_id = message.get("id")

        if msg_id is not None and msg_id == self._selected_message_id:
            return

        self._selected_message = message
        self._selected_message_id = msg_id

        body, actions = _parse_content(message.get("content", ""))
        try:
            md = self.query_one("#inbox-content", Markdown)
            md.update(body)
        except Exception:
            pass

        self._update_action_bar(actions)

    def _update_action_bar(self, actions: list[dict]) -> None:
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
        self._report = report
        self._messages = report.get("messages", [])
        # Keep selected message in sync if still present
        if self._selected_message is not None:
            selected_id = self._selected_message.get("id")
            for m in self._messages:
                if m.get("id") == selected_id:
                    self._selected_message = m
                    _, actions = _parse_content(m.get("content", ""))
                    self._update_action_bar(actions)
                    break
        self._refresh_list()

    def _refresh(self) -> None:
        self._messages = self._report.get("messages", [])
        self._refresh_list()
