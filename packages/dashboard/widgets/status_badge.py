"""Status badge widget for displaying agent/task status."""

from textual.widgets import Static


SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class StatusBadge(Static):
    """A colored inline badge showing agent or task status.

    Possible statuses and their badges:
    - "running"     → "⠋ RUN" (animated spinner, green)
    - "paused"      → "PAUSE" (magenta)
    - "idle(...)"   → "BLOCK" (yellow) — blocked idle
    - "orphaned"    → "ORPH"  (red)   — agent gone missing
    - anything else → "IDLE"  (green)
    """

    def __init__(self, status: str, **kwargs: object) -> None:
        self._status = status
        self._spinner_index = 0
        text, css_class = _badge_for(status)
        super().__init__(text, **kwargs)
        self.add_class("status-badge")
        self.add_class(css_class)

    def on_mount(self) -> None:
        if self._status == "running":
            self.set_interval(0.1, self._tick_spinner)

    def _tick_spinner(self) -> None:
        self._spinner_index = (self._spinner_index + 1) % len(SPINNER_FRAMES)
        frame = SPINNER_FRAMES[self._spinner_index]
        self.update(f"{frame} RUN")


def _badge_for(status: str) -> tuple[str, str]:
    """Return (badge_text, css_class) for a given status string."""
    if status == "running":
        frame = SPINNER_FRAMES[0]
        return f"{frame} RUN", "badge--running"
    elif status == "paused":
        return "PAUSE", "badge--paused"
    elif status.startswith("idle("):
        return "BLOCK", "badge--blocked"
    elif status == "orphaned":
        return "ORPH", "badge--orphaned"
    else:
        return "IDLE", "badge--idle"
