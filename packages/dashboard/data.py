"""Data layer for the Octopoid dashboard.

Wraps orchestrator.reports.get_project_report() for use by Textual widgets.
Data is fetched synchronously in a background thread (via Textual's @work).
"""

from typing import Any


class DataManager:
    """Fetches and caches the project report from the Octopoid API."""

    def fetch_sync(self) -> dict[str, Any]:
        """Fetch the full project report synchronously.

        Intended to be called from a background thread via Textual's @work.

        Returns:
            Report dict with keys: work, done_tasks, prs, proposals,
            messages, agents, health, generated_at.

        Raises:
            RuntimeError: If the SDK is not installed or not configured.
        """
        from orchestrator.sdk import get_sdk
        from orchestrator.reports import get_project_report

        sdk = get_sdk()
        return get_project_report(sdk)
