"""Status badge widget for displaying agent/task status."""

from textual.widgets import Static


class StatusBadge(Static):
    """A colored inline badge showing agent or task status.

    Possible statuses and their badges:
    - "running"     → "RUN"   (green)
    - "paused"      → "PAUSE" (magenta)
    - "idle(...)"   → "BLOCK" (yellow) — blocked idle
    - "orphaned"    → "ORPH"  (red)   — agent gone missing
    - anything else → "IDLE"  (green)
    """

    def __init__(self, status: str, **kwargs: object) -> None:
        text, css_class = _badge_for(status)
        super().__init__(text, **kwargs)
        self.add_class("status-badge")
        self.add_class(css_class)


def _badge_for(status: str) -> tuple[str, str]:
    """Return (badge_text, css_class) for a given status string."""
    if status == "running":
        return "RUN", "badge--running"
    elif status == "paused":
        return "PAUSE", "badge--paused"
    elif status.startswith("idle("):
        return "BLOCK", "badge--blocked"
    elif status == "orphaned":
        return "ORPH", "badge--orphaned"
    else:
        return "IDLE", "badge--idle"
