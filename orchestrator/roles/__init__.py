"""Agent roles for the orchestrator."""

from .base import BaseRole
from .github_issue_monitor import GitHubIssueMonitorRole

__all__ = [
    "BaseRole",
    "GitHubIssueMonitorRole",
]
