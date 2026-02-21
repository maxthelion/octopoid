"""Base class for all dashboard tab widgets."""

from __future__ import annotations

from textual.widget import Widget


class TabBase(Widget):
    DEFAULT_CSS = """
    TabBase { height: 100%; }
    """

    def __init__(self, report: dict | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._report = report or {}

    def update_data(self, report: dict) -> None:
        self._report = report
        self._refresh()

    def _refresh(self) -> None:
        """Override in subclasses to update UI from self._report."""
        pass
