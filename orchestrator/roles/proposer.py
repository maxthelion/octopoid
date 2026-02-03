"""Proposer role - specialized agents that propose work for the curator."""

from ..config import get_proposal_limits
from ..proposal_utils import can_create_proposal, get_rejected_proposals
from .base import main_entry
from .specialist import SpecialistRole


class ProposerRole(SpecialistRole):
    """Proposer that analyzes the codebase and creates proposals.

    Proposers are specialized agents with a specific focus area (tests,
    architecture, features, plans). They run infrequently and create
    proposals that the curator will evaluate.

    Inherits from SpecialistRole which provides:
    - focus area configuration
    - domain-specific prompt loading
    """

    def __init__(self):
        super().__init__()
        self.proposer_type = self.agent_name

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

        # Load domain-specific prompt (from SpecialistRole)
        domain_prompt = self.get_focus_prompt()
        focus_description = self.get_focus_description()

        # Build prompt for Claude
        instructions = self.read_instructions()

        prompt = f"""You are a proposer agent.

**Focus Area:** {focus_description}

{instructions}

{domain_prompt}

{rejections_text}

## Your Task

Analyze the codebase from your specialized perspective and create up to {max_per_run} proposals.

For each proposal you want to create, use the /create-proposal skill.

### Guidelines

1. **Review rejections first** - Don't repeat mistakes from rejected proposals
2. **Stay focused** - Only propose work within your focus area
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
