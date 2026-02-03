"""Curator role - evaluates proposals and promotes them to tasks."""

import sys
from pathlib import Path

from ..config import get_prompts_dir
from ..message_utils import warning
from ..proposal_utils import (
    detect_conflicts,
    get_active_proposals,
    get_deferred_proposals,
    score_proposal,
)
from ..queue_utils import can_create_task, create_task
from .base import BaseRole, main_entry


class CuratorRole(BaseRole):
    """Curator that evaluates proposals and promotes them to the task queue.

    The curator does NOT explore the codebase directly. Instead, it:
    - Scores proposals based on configurable weights
    - Promotes good proposals to the task queue
    - Rejects proposals with feedback
    - Defers proposals that aren't right for now
    - Escalates conflicts to the project owner
    """

    def get_curator_prompt(self) -> str:
        """Load the curator prompt if customized.

        Returns:
            Prompt content or empty string if not found
        """
        prompts_dir = get_prompts_dir()
        prompt_file = prompts_dir / "curator.md"

        if prompt_file.exists():
            return prompt_file.read_text()

        return ""

    def format_proposals_for_review(self, proposals: list[dict]) -> str:
        """Format proposals for the curator to review.

        Args:
            proposals: List of proposal dictionaries

        Returns:
            Formatted markdown string
        """
        if not proposals:
            return "No active proposals to review."

        lines = ["## Active Proposals", ""]

        for proposal in proposals:
            # Calculate initial score
            score = score_proposal(proposal)

            lines.append(f"### {proposal.get('title', 'Unknown')}")
            lines.append(f"**ID:** {proposal.get('id', 'unknown')}")
            lines.append(f"**Proposer:** {proposal.get('proposer', 'unknown')}")
            lines.append(f"**Category:** {proposal.get('category', 'unknown')}")
            lines.append(f"**Complexity:** {proposal.get('complexity', 'M')}")
            lines.append(f"**Initial Score:** {score:.2f}")
            lines.append(f"**File:** {proposal.get('path', 'unknown')}")
            lines.append("")

            # Include summary if available
            content = proposal.get("content", "")
            summary_match = content.find("## Summary")
            if summary_match != -1:
                summary_end = content.find("##", summary_match + 10)
                if summary_end == -1:
                    summary_end = len(content)
                summary = content[summary_match + 11 : summary_end].strip()
                lines.append(f"**Summary:** {summary[:200]}...")
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def format_conflicts(self, conflicts: list[tuple[dict, dict, str]]) -> str:
        """Format detected conflicts for escalation.

        Args:
            conflicts: List of (proposal1, proposal2, description) tuples

        Returns:
            Formatted markdown string
        """
        if not conflicts:
            return ""

        lines = ["## Detected Conflicts", ""]
        lines.append("The following proposals may conflict and need human decision:")
        lines.append("")

        for p1, p2, desc in conflicts:
            lines.append(f"### Conflict: {desc}")
            lines.append(f"- **Proposal 1:** {p1.get('id')} - {p1.get('title')}")
            lines.append(f"- **Proposal 2:** {p2.get('id')} - {p2.get('title')}")
            lines.append("")

        return "\n".join(lines)

    def escalate_conflict(self, p1: dict, p2: dict, description: str) -> None:
        """Escalate a conflict to the project owner via message.

        Args:
            p1: First conflicting proposal
            p2: Second conflicting proposal
            description: Conflict description
        """
        body = f"""Two proposals appear to conflict and need your decision:

## Conflict
{description}

## Proposal 1: {p1.get('title')}
- **ID:** {p1.get('id')}
- **Proposer:** {p1.get('proposer')}
- **Category:** {p1.get('category')}

## Proposal 2: {p2.get('title')}
- **ID:** {p2.get('id')}
- **Proposer:** {p2.get('proposer')}
- **Category:** {p2.get('category')}

## Action Needed
Please review both proposals and either:
1. Approve one and reject the other
2. Modify one to resolve the conflict
3. Reject both and create a combined approach

Both proposals have been deferred pending your decision.
"""

        warning(
            f"Conflict: {p1.get('id')} vs {p2.get('id')}",
            body,
            self.agent_name,
        )

    def run(self) -> int:
        """Evaluate proposals and promote/reject/defer them.

        Returns:
            Exit code (0 for success)
        """
        # Get active proposals
        proposals = get_active_proposals()

        if not proposals:
            self.log("No active proposals to curate")
            return 0

        self.log(f"Evaluating {len(proposals)} active proposals")

        # Check for conflicts first
        conflicts = detect_conflicts(proposals)
        if conflicts:
            self.log(f"Detected {len(conflicts)} potential conflicts")
            for p1, p2, desc in conflicts:
                self.escalate_conflict(p1, p2, desc)

        # Load curator prompt
        curator_prompt = self.get_curator_prompt()

        # Format proposals for review
        proposals_text = self.format_proposals_for_review(proposals)
        conflicts_text = self.format_conflicts(conflicts)

        # Check task queue backpressure
        can_create, bp_reason = can_create_task()
        backpressure_note = ""
        if not can_create:
            backpressure_note = f"""
## Backpressure Warning
The task queue is currently full: {bp_reason}
Consider deferring proposals rather than promoting them until the queue clears.
"""

        # Build prompt for Claude
        instructions = self.read_instructions()

        prompt = f"""You are a curator agent that evaluates proposals and decides their fate.

{instructions}

{curator_prompt}

{proposals_text}

{conflicts_text}

{backpressure_note}

## Your Task

For each proposal, decide one of:
1. **Promote** - Use /promote-proposal to move it to the task queue
2. **Reject** - Use /reject-proposal with constructive feedback
3. **Defer** - Use /defer-proposal for proposals that aren't right for now

### Decision Guidelines

**Promote if:**
- Aligns with current project priorities
- Well-scoped and actionable
- Dependencies are met
- No unresolved conflicts

**Reject if:**
- Out of scope for the project
- Poorly defined or too vague
- Fundamentally flawed approach
- Duplicate of existing work
(Always provide feedback so the proposer can learn)

**Defer if:**
- Good idea but wrong timing
- Blocked by other work
- Part of a conflict being escalated
- Queue is under backpressure

### Scoring Factors
The initial score considers:
- priority_alignment: Does it match project goals?
- complexity_reduction: Does it simplify the codebase?
- risk: What's the blast radius if wrong?
- dependencies_met: Are blockers resolved?
- voice_weight: Proposer's trust level

Use these as guidance but apply your judgment.

### Conflict Handling
For conflicting proposals, defer both and they will be escalated to the project owner.

Process each proposal and make a decision.
"""

        # Invoke Claude with appropriate tools
        allowed_tools = [
            "Read",
            "Skill",  # For /promote-proposal, /reject-proposal, /defer-proposal
        ]

        exit_code, stdout, stderr = self.invoke_claude(
            prompt,
            allowed_tools=allowed_tools,
            max_turns=30,  # May need many turns for multiple proposals
        )

        if exit_code != 0:
            self.log(f"Claude invocation failed: {stderr}")
            return exit_code

        self.log("Curation complete")
        return 0


def main():
    main_entry(CuratorRole)


if __name__ == "__main__":
    main()
