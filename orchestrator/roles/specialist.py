"""Specialist base class for proposers and gatekeepers."""

import os
from pathlib import Path

from ..config import get_prompts_dir, get_templates_dir
from .base import BaseRole


class SpecialistRole(BaseRole):
    """Base class for specialist roles (proposers and gatekeepers).

    Specialists have a focus area and use domain-specific prompts.
    Both proposers (proactive) and gatekeepers (reactive) share this base.
    """

    def __init__(self):
        super().__init__()
        # Get focus from environment (set by scheduler based on agent config)
        self.focus = os.environ.get("AGENT_FOCUS", "general")

    def get_focus_prompt(self) -> str:
        """Load the domain-specific prompt for this focus area.

        Looks for prompts in this order:
        1. .orchestrator/prompts/{agent_name}.md (most specific)
        2. .orchestrator/prompts/{focus}.md (focus-specific)
        3. orchestrator/templates/proposer-{focus}.md (defaults)

        Returns:
            Prompt content or empty string if not found
        """
        prompts_dir = get_prompts_dir()
        templates_dir = get_templates_dir()

        # Try agent-specific prompt first
        agent_prompt = prompts_dir / f"{self.agent_name}.md"
        if agent_prompt.exists():
            return agent_prompt.read_text()

        # Try focus-specific prompt
        focus_prompt = prompts_dir / f"{self.focus}.md"
        if focus_prompt.exists():
            return focus_prompt.read_text()

        # Fall back to template
        template_prompt = templates_dir / f"proposer-{self.focus}.md"
        if template_prompt.exists():
            return template_prompt.read_text()

        # Try gatekeeper template as fallback
        gk_template = templates_dir / f"gatekeeper-{self.focus}.md"
        if gk_template.exists():
            return gk_template.read_text()

        return ""

    def get_focus_description(self) -> str:
        """Get a human-readable description of the focus area.

        Returns:
            Description string
        """
        descriptions = {
            "test_quality": "Test quality: flaky tests, coverage, assertions",
            "code_structure": "Code structure: architecture, patterns, coupling",
            "code_style": "Code style: naming, formatting, conventions",
            "features": "Features: new functionality, user experience",
            "security": "Security: vulnerabilities, secrets, dependencies",
            "plans": "Plans: tasks from project documentation",
            "lint": "Linting: code quality, static analysis",
            "tests": "Tests: test coverage, test quality",
            "architecture": "Architecture: design patterns, boundaries",
            "style": "Style: code conventions, consistency",
        }
        return descriptions.get(self.focus, f"Focus area: {self.focus}")
