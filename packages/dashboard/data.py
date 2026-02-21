"""Data layer for the Octopoid dashboard.

Wraps orchestrator.reports.get_project_report() for use by Textual widgets.
Data is fetched synchronously in a background thread (via Textual's @work).
"""

from typing import Any


class DataManager:
    """Fetches and caches the project report from the Octopoid API."""

    def poll_sync(self) -> dict[str, Any]:
        """Poll for queue counts without fetching full task lists.

        Intended to be called from a background thread via Textual's @work.
        Used to detect changes before deciding whether to do a full fetch.

        Returns:
            Dict with 'queue_counts' key mapping queue names to integer counts,
            plus 'provisional_tasks' and 'orchestrator_registered'.

        Raises:
            RuntimeError: If the SDK is not installed or not configured.
        """
        from orchestrator.sdk import get_sdk, get_orchestrator_id

        sdk = get_sdk()
        orch_id = get_orchestrator_id()
        return sdk.poll(orchestrator_id=orch_id)

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
