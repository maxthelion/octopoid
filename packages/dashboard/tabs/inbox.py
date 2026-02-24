"""Inbox tab — server-sourced messages addressed to the human, master-detail view.

Left panel: scrollable message list (newest first) showing type tag, a
human-readable title, and a relative timestamp. Right panel: full message
content rendered as Markdown, optionally preceded by the referenced entity
content (draft / task). Fixed action bar at bottom: buttons parsed from
content JSON, plus free-text input.

Clicking an action button posts an action_command back via sdk.messages.create().
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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

# Human-readable fallback titles per message type
_TYPE_TITLES: dict[str, str] = {
    "action_proposal": "Proposal",
    "action_command": "Command",
    "worker_result": "Result",
    "info": "Info",
    "warning": "Warning",
    "error": "Error",
}

# Actor display names
_ACTOR_NAMES: dict[str, str] = {
    "agent": "Agent",
    "human": "Human",
    "orchestrator": "Orchestrator",
    "system": "System",
}

# Number of fixed action button slots
_NUM_ACTION_SLOTS = 3
_ACTION_HOTKEYS = ["A", "B", "C"]


def _actor_display(actor: str) -> str:
    """Return a human-readable actor name."""
    if not actor:
        return "Unknown"
    return _ACTOR_NAMES.get(actor.lower(), actor.capitalize())


def _rel_time(iso_str: str | None) -> str:
    """Format an ISO timestamp as a short relative time string.

    Examples: 'just now', '5m ago', '3h ago', 'yesterday', '2d ago'.
    Returns empty string if iso_str is absent or unparseable.
    """
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        total_secs = (now - dt).total_seconds()
        if total_secs < 60:
            return "just now"
        mins = int(total_secs / 60)
        if mins < 60:
            return f"{mins}m ago"
        hours = mins // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days == 1:
            return "yesterday"
        return f"{days}d ago"
    except (ValueError, TypeError):
        return ""


def _parse_content(content: str) -> tuple[str, list[dict]]:
    """Parse a message's content field.

    Supports both the structured InboxMessage schema (title/summary/message_type/…)
    and the legacy freeform formats (body/text/message keys, or plain text).

    Returns:
        (body_markdown, actions) where body_markdown is the text to display
        and actions is a list of action dicts (may be empty).
    """
    if not content:
        return "_empty_", []

    try:
        data = json.loads(content)
        if isinstance(data, dict):
            actions = data.get("actions", [])
            if not isinstance(actions, list):
                actions = []

            # --- Structured InboxMessage schema (title + summary) ---
            if "title" in data or "message_type" in data:
                parts: list[str] = []
                title = data.get("title", "")
                summary = data.get("summary", "")
                entity_type = data.get("entity_type", "")
                entity_id = data.get("entity_id", "")
                if title:
                    parts.append(f"## {title}")
                if summary:
                    parts.append(summary)
                if entity_type and entity_id is not None:
                    parts.append(f"*{entity_type.capitalize()} #{entity_id}*")
                body = "\n\n".join(parts) if parts else "_empty_"
                return body, actions

            # --- Legacy format: body/text/message key ---
            body = data.get("body") or data.get("text") or data.get("message") or ""
            if not body:
                # Fall back to pretty-printing the JSON minus display-only keys
                skip = {"actions", "title", "entity_type", "entity_id", "subject"}
                display_data = {k: v for k, v in data.items() if k not in skip}
                body = f"```json\n{json.dumps(display_data, indent=2)}\n```" if display_data else content
            return body or "_empty_", actions
    except (json.JSONDecodeError, ValueError):
        pass

    # Plain text content
    return content, []


def _extract_title(message: dict) -> str:
    """Extract or synthesize a human-readable title for the message list.

    Priority:
    1. Explicit 'title' or 'subject' field in content JSON (structured schema).
    2. First non-empty line of 'body'/'text'/'message' in content JSON.
    3. First line of plain-text content.
    4. Type-based fallback ('Proposal', 'Result', etc.).
    """
    content = message.get("content", "")
    msg_type = message.get("type", "")

    if content:
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                title = data.get("title") or data.get("subject") or ""
                if title:
                    s = str(title)
                    return s if len(s) <= 80 else s[:77] + "…"
                body = data.get("body") or data.get("text") or data.get("message") or ""
                if body:
                    first_line = body.strip().split("\n")[0].lstrip("#").strip()
                    if first_line:
                        return first_line if len(first_line) <= 80 else first_line[:77] + "…"
        except (json.JSONDecodeError, ValueError):
            # Plain text — use first line as title
            first_line = content.strip().split("\n")[0]
            if first_line:
                return first_line if len(first_line) <= 80 else first_line[:77] + "…"

    return _TYPE_TITLES.get(msg_type, "Message")


def _get_entity_ref(content: str) -> tuple[str | None, str | None]:
    """Extract (entity_type, entity_id) from structured message content JSON.

    Returns (None, None) if content is not structured or has no entity reference.
    """
    if not content:
        return None, None
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            entity_type = data.get("entity_type")
            entity_id = data.get("entity_id")
            if entity_type and entity_id:
                return str(entity_type), str(entity_id)
    except (json.JSONDecodeError, ValueError):
        pass
    return None, None


def _load_entity_content(entity_type: str, entity_id: str, report: dict) -> str | None:
    """Resolve entity content from the cached report.

    Returns markdown-formatted content, or None if the entity is not found.
    Does not make additional network calls.
    """
    if entity_type == "draft":
        for draft in report.get("drafts", []):
            if str(draft.get("id", "")) == entity_id:
                file_path = draft.get("file_path") or ""
                if file_path:
                    try:
                        return Path(file_path).read_text()
                    except OSError:
                        pass
                title = draft.get("title") or "Untitled"
                status = draft.get("status") or "unknown"
                return f"# {title}\n\n**Status:** {status}\n\n*Draft file not available.*"
        return None

    if entity_type == "task":
        all_tasks = report.get("work", []) + report.get("done_tasks", [])
        for task in all_tasks:
            if str(task.get("id", "")) == entity_id:
                title = task.get("title") or task.get("name") or f"Task {entity_id}"
                status = task.get("status") or task.get("flow") or "unknown"
                return f"# {title}\n\n**Status:** {status}"
        return None

    return None


def _build_detail_content(message: dict, report: dict) -> str:
    """Build the full markdown content string for the detail panel.

    Includes a metadata header (from, time), optional entity content, and
    the message body.
    """
    body, _ = _parse_content(message.get("content", ""))
    title = _extract_title(message)
    actor_name = _actor_display(message.get("from_actor", ""))
    rel_time = _rel_time(message.get("created_at"))

    lines: list[str] = [f"# {title}", ""]

    meta_parts: list[str] = []
    if actor_name:
        meta_parts.append(f"**From:** {actor_name}")
    if rel_time:
        meta_parts.append(f"**Time:** {rel_time}")
    if meta_parts:
        lines.append("  ".join(meta_parts))
        lines.append("")

    # Entity content (from structured schema entity_type/entity_id fields)
    entity_type, entity_id = _get_entity_ref(message.get("content", ""))
    if entity_type and entity_id:
        entity_content = _load_entity_content(entity_type, entity_id, report)
        if entity_content:
            lines += ["---", "", f"*Referenced {entity_type}:*", "", entity_content, ""]

    # Message body
    if body and body != "_empty_":
        lines += ["---", "", body]

    return "\n".join(lines)


def _content_preview(content: str, max_len: int = 60) -> str:
    """Return a short preview of message content for list display."""
    body, _ = _parse_content(content)
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
    """A single message entry in the left list.

    Displays: [TYPE TAG] human-readable title  Actor · relative-time
    """

    def __init__(self, message: dict, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._message = message

    @property
    def message_data(self) -> dict:
        return self._message

    def compose(self) -> ComposeResult:
        msg_type = self._message.get("type", "")
        from_actor = self._message.get("from_actor", "")
        created_at = self._message.get("created_at")

        tag, color = _TYPE_TAGS.get(msg_type, ("MSG", "#e0e0e0"))
        title = _extract_title(self._message)
        actor_name = _actor_display(from_actor)
        rel_time = _rel_time(created_at)

        label_text = Text()
        label_text.append(f"{tag} ", style=f"bold {color}")
        label_text.append(title, style="#e0e0e0")

        meta_parts: list[str] = []
        if actor_name:
            meta_parts.append(actor_name)
        if rel_time:
            meta_parts.append(rel_time)
        if meta_parts:
            label_text.append(f"  {' · '.join(meta_parts)}", style="#616161")

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

        self._render_detail(message)

    def _render_detail(self, message: dict) -> None:
        """Update the right panel with the full detail view for a message."""
        detail = _build_detail_content(message, self._report)
        try:
            md = self.query_one("#inbox-content", Markdown)
            md.update(detail)
        except Exception:
            pass

        _, actions = _parse_content(message.get("content", ""))
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
        # Keep selected message in sync and refresh the detail view
        if self._selected_message is not None:
            selected_id = self._selected_message.get("id")
            for m in self._messages:
                if m.get("id") == selected_id:
                    self._selected_message = m
                    self._render_detail(m)
                    break
        self._refresh_list()

    def _refresh(self) -> None:
        self._messages = self._report.get("messages", [])
        self._refresh_list()
