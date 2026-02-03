"""Agent roles for the orchestrator."""

from .base import BaseRole
from .curator import CuratorRole
from .gatekeeper import GatekeeperRole
from .gatekeeper_coordinator import GatekeeperCoordinatorRole
from .implementer import ImplementerRole
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
    # Gatekeeper system
    "GatekeeperRole",
    "GatekeeperCoordinatorRole",
    # Execution layer (both models)
    "ImplementerRole",
    "TesterRole",
    "ReviewerRole",
]
