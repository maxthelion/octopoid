"""Agent roles for the orchestrator."""

from .base import BaseRole
from .curator import CuratorRole
from .implementer import ImplementerRole
from .product_manager import ProductManagerRole
from .proposer import ProposerRole
from .reviewer import ReviewerRole
from .tester import TesterRole

__all__ = [
    "BaseRole",
    # Task model (v1)
    "ProductManagerRole",
    # Proposal model (v2)
    "ProposerRole",
    "CuratorRole",
    # Execution layer (both models)
    "ImplementerRole",
    "TesterRole",
    "ReviewerRole",
]
