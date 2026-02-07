"""Agent roles for the orchestrator."""

from .base import BaseRole
from .check_runner import CheckRunnerRole
from .curator import CuratorRole
from .gatekeeper import GatekeeperRole
from .gatekeeper_coordinator import GatekeeperCoordinatorRole
from .implementer import ImplementerRole
from .orchestrator_impl import OrchestratorImplRole
from .product_manager import ProductManagerRole
from .proposer import ProposerRole
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
    # Check system
    "CheckRunnerRole",
    # Gatekeeper system
    "GatekeeperRole",
    "GatekeeperCoordinatorRole",
    # Execution layer (both models)
    "ImplementerRole",
    "OrchestratorImplRole",
    "TesterRole",
    "ReviewerRole",
]
