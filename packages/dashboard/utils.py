"""Shared utility functions for the dashboard package."""

from __future__ import annotations

from datetime import datetime, timezone


def format_age(iso_str: str | None) -> str:
    """Format an ISO timestamp as a human-readable age like '2h', '15m'."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None)
        delta = datetime.now() - dt
        secs = delta.total_seconds()
        if secs < 0:
            return "now"
        if secs < 60:
            return f"{int(secs)}s"
        if secs < 3600:
            return f"{int(secs // 60)}m"
        if secs < 86400:
            return f"{int(secs // 3600)}h"
        return f"{int(secs // 86400)}d"
    except (ValueError, TypeError):
        return ""


def time_ago(iso_str: str | None) -> str | None:
    """Convert an ISO timestamp to a relative time string like '5m ago'."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        mins = int((now - dt).total_seconds() / 60)
        if mins < 0:
            return "just now"
        if mins < 60:
            return f"{mins}m ago"
        hours = mins // 60
        remaining = mins % 60
        if hours < 24:
            return f"{hours}h {remaining}m ago"
        days = hours // 24
        return f"{days}d {hours % 24}h ago"
    except (ValueError, TypeError):
        return None
