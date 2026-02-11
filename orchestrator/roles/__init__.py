"""Agent roles for the orchestrator."""

from .base import BaseRole
from .curator import CuratorRole
from .gatekeeper import GatekeeperRole
from .gatekeeper_coordinator import GatekeeperCoordinatorRole
from .github_issue_monitor import GitHubIssueMonitorRole
from .implementer import ImplementerRole
from .orchestrator_impl import OrchestratorImplRole
from .pr_coordinator import PRCoordinatorRole
from .product_manager import ProductManagerRole
from .proposer import ProposerRole
from .rebaser import RebaserRole
from .reviewer import ReviewerRole
from .specialist import SpecialistRole
from .tester import TesterRole

__all__ = [
    "BaseRole",
    "SpecialistRole",
    # Task model (v1)
    "ProductManagerRole",
    # Proposal model (v2)
    "ProposerRole",
    "CuratorRole",
    "RebaserRole",
    # Gatekeeper system
    "GatekeeperRole",
    "GatekeeperCoordinatorRole",
    # PR coordination
    "PRCoordinatorRole",
    # Execution layer (both models)
    "ImplementerRole",
    "OrchestratorImplRole",
    "TesterRole",
    "ReviewerRole",
    # Monitoring and automation
    "GitHubIssueMonitorRole",
]
