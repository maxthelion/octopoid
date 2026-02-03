"""Proposer role - specialized agents that propose work for the curator."""

import sys
from pathlib import Path

from ..config import get_prompts_dir, get_proposal_limits
from ..proposal_utils import can_create_proposal, get_rejected_proposals
from .base import BaseRole, main_entry


class ProposerRole(BaseRole):
    """Proposer that analyzes the codebase and creates proposals.

    Proposers are specialized agents with a specific focus area (tests,
    architecture, features, plans). They run infrequently and create
    proposals that the curator will evaluate.
    """

    def __init__(self):
        super().__init__()
        # Get focus from environment (set by scheduler based on agent config)
        import os

        self.focus = os.environ.get("AGENT_FOCUS", "general")
        self.proposer_type = os.environ.get("AGENT_NAME", "proposer")

    def get_proposer_prompt(self) -> str:
        """Load the domain-specific prompt for this proposer.

        Returns:
            Prompt content or empty string if not found
        """
        prompts_dir = get_prompts_dir()
        prompt_file = prompts_dir / f"{self.proposer_type}.md"

        if prompt_file.exists():
            return prompt_file.read_text()

        # Fallback to focus-based prompt
        focus_prompt = prompts_dir / f"{self.focus}.md"
        if focus_prompt.exists():
            return focus_prompt.read_text()

        return ""

    def format_rejections_for_review(self, rejections: list[dict]) -> str:
        """Format rejected proposals for the proposer to review.

        Args:
            rejections: List of rejected proposal dictionaries

        Returns:
            Formatted markdown string
        """
        if not rejections:
            return "No previous rejections to review."

        lines = ["## Previous Rejected Proposals", ""]
        lines.append("Review these before proposing new items to avoid similar issues:")
        lines.append("")

        for rej in rejections[:5]:  # Show last 5
            lines.append(f"### {rej.get('title', 'Unknown')}")
            lines.append(f"**ID:** {rej.get('id', 'unknown')}")
            lines.append(f"**Rejected:** {rej.get('rejected_at', 'unknown')}")
            if rej.get("rejection_reason"):
                lines.append(f"**Reason:** {rej['rejection_reason']}")
            lines.append("")

        return "\n".join(lines)

    def run(self) -> int:
        """Analyze the codebase and create proposals.

        Returns:
            Exit code (0 for success)
        """
        # Check backpressure
        can_create, reason = can_create_proposal(self.proposer_type)
        if not can_create:
            self.log(f"Backpressure: {reason}")
            return 0  # Not an error, just nothing to do

        # Get proposal limits
        limits = get_proposal_limits(self.proposer_type)
        max_per_run = limits["max_per_run"]

        # Review previous rejections
        rejections = get_rejected_proposals(self.proposer_type)
        rejections_text = self.format_rejections_for_review(rejections)

        # Load domain-specific prompt
        domain_prompt = self.get_proposer_prompt()

        # Build prompt for Claude
        instructions = self.read_instructions()

        prompt = f"""You are a proposer agent with focus area: {self.focus}

{instructions}

{domain_prompt}

{rejections_text}

## Your Task

Analyze the codebase from your specialized perspective and create up to {max_per_run} proposals.

For each proposal you want to create, use the /create-proposal skill.

### Guidelines

1. **Review rejections first** - Don't repeat mistakes from rejected proposals
2. **Stay focused** - Only propose work within your focus area ({self.focus})
3. **Be specific** - Proposals should be actionable and well-scoped
4. **Consider dependencies** - Note what must happen first
5. **Explain value** - Why does this matter? What does it unblock?

### Proposal Categories
- test: Test quality improvements
- refactor: Code structure improvements
- feature: New functionality
- debt: Technical debt reduction
- plan-task: Tasks extracted from project plans

### Complexity Guidelines
- S: Few hours, single file
- M: Day or two, few files
- L: Several days, multiple components
- XL: Week+, architectural changes

Start by exploring the codebase relevant to your focus, then create proposals.
"""

        # Invoke Claude with appropriate tools
        allowed_tools = [
            "Read",
            "Glob",
            "Grep",
            "Bash",  # For git commands
            "Skill",  # For /create-proposal
        ]

        exit_code, stdout, stderr = self.invoke_claude(
            prompt,
            allowed_tools=allowed_tools,
            max_turns=20,
        )

        if exit_code != 0:
            self.log(f"Claude invocation failed: {stderr}")
            return exit_code

        self.log("Proposal generation complete")
        return 0


def main():
    main_entry(ProposerRole)


if __name__ == "__main__":
    main()
