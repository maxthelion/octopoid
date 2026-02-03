"""Proposal management for the proposal-driven model."""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .config import (
    get_curator_scoring,
    get_proposal_limits,
    get_proposals_dir,
    get_voice_weight,
)

ProposalStatus = Literal["active", "promoted", "deferred", "rejected"]
ProposalCategory = Literal["test", "refactor", "feature", "debt", "plan-task"]
ProposalComplexity = Literal["S", "M", "L", "XL"]


def get_proposal_subdir(status: ProposalStatus) -> Path:
    """Get a specific proposal subdirectory.

    Args:
        status: One of 'active', 'promoted', 'deferred', 'rejected'

    Returns:
        Path to the subdirectory
    """
    proposals_dir = get_proposals_dir()
    path = proposals_dir / status
    path.mkdir(parents=True, exist_ok=True)
    return path


def count_proposals(status: ProposalStatus, proposer: str | None = None) -> int:
    """Count proposals in a subdirectory, optionally filtered by proposer.

    Args:
        status: Proposal status directory
        proposer: Optional proposer name to filter by

    Returns:
        Number of proposals
    """
    path = get_proposal_subdir(status)
    count = 0

    for proposal_file in path.glob("*.md"):
        if proposer:
            info = parse_proposal_file(proposal_file)
            if info and info.get("proposer") == proposer:
                count += 1
        else:
            count += 1

    return count


def can_create_proposal(proposer_type: str) -> tuple[bool, str]:
    """Check if a proposer can create a new proposal (backpressure check).

    Args:
        proposer_type: The proposer type (e.g., "test-checker")

    Returns:
        Tuple of (can_create, reason_if_not)
    """
    limits = get_proposal_limits(proposer_type)
    active_count = count_proposals("active", proposer_type)

    if active_count >= limits["max_active"]:
        return False, f"Proposal limit reached: {active_count} active (limit: {limits['max_active']})"

    return True, ""


def list_proposals(status: ProposalStatus, proposer: str | None = None) -> list[dict[str, Any]]:
    """List proposals with metadata.

    Args:
        status: Proposal status directory
        proposer: Optional proposer name to filter by

    Returns:
        List of proposal dictionaries
    """
    path = get_proposal_subdir(status)
    proposals = []

    for proposal_file in path.glob("*.md"):
        info = parse_proposal_file(proposal_file)
        if info:
            if proposer and info.get("proposer") != proposer:
                continue
            proposals.append(info)

    # Sort by created time
    proposals.sort(key=lambda p: p.get("created", ""), reverse=True)
    return proposals


def parse_proposal_file(proposal_path: Path) -> dict[str, Any] | None:
    """Parse a proposal file and extract metadata.

    Args:
        proposal_path: Path to the proposal .md file

    Returns:
        Dictionary with proposal metadata or None if invalid
    """
    try:
        content = proposal_path.read_text()
    except IOError:
        return None

    # Extract proposal ID from title
    title_match = re.search(r"^#\s*Proposal:\s*(.+)$", content, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else proposal_path.stem

    # Extract fields
    id_match = re.search(r"^\*\*ID:\*\*\s*(.+)$", content, re.MULTILINE)
    proposer_match = re.search(r"^\*\*Proposer:\*\*\s*(.+)$", content, re.MULTILINE)
    category_match = re.search(r"^\*\*Category:\*\*\s*(.+)$", content, re.MULTILINE)
    complexity_match = re.search(r"^\*\*Complexity:\*\*\s*(.+)$", content, re.MULTILINE)
    created_match = re.search(r"^\*\*Created:\*\*\s*(.+)$", content, re.MULTILINE)

    # Rejection fields
    rejected_by_match = re.search(r"^\*\*Rejected By:\*\*\s*(.+)$", content, re.MULTILINE)
    rejected_at_match = re.search(r"^\*\*Rejected At:\*\*\s*(.+)$", content, re.MULTILINE)
    rejection_reason_match = re.search(
        r"^\*\*Rejection Reason:\*\*\s*\|?\s*\n([\s\S]*?)(?=\n\*\*|\n##|\Z)",
        content,
        re.MULTILINE,
    )

    proposal_id = id_match.group(1).strip() if id_match else proposal_path.stem

    result = {
        "path": proposal_path,
        "id": proposal_id,
        "title": title,
        "proposer": proposer_match.group(1).strip() if proposer_match else None,
        "category": category_match.group(1).strip() if category_match else None,
        "complexity": complexity_match.group(1).strip() if complexity_match else "M",
        "created": created_match.group(1).strip() if created_match else None,
        "content": content,
    }

    if rejected_by_match:
        result["rejected_by"] = rejected_by_match.group(1).strip()
    if rejected_at_match:
        result["rejected_at"] = rejected_at_match.group(1).strip()
    if rejection_reason_match:
        result["rejection_reason"] = rejection_reason_match.group(1).strip()

    return result


def get_active_proposals() -> list[dict[str, Any]]:
    """Get all active proposals.

    Returns:
        List of active proposal dictionaries
    """
    return list_proposals("active")


def get_rejected_proposals(proposer: str) -> list[dict[str, Any]]:
    """Get rejected proposals for a specific proposer.

    Args:
        proposer: Proposer name

    Returns:
        List of rejected proposal dictionaries with feedback
    """
    return list_proposals("rejected", proposer)


def get_deferred_proposals() -> list[dict[str, Any]]:
    """Get all deferred proposals.

    Returns:
        List of deferred proposal dictionaries
    """
    return list_proposals("deferred")


def create_proposal(
    title: str,
    proposer: str,
    category: ProposalCategory,
    complexity: ProposalComplexity,
    summary: str,
    rationale: str,
    acceptance_criteria: list[str],
    relevant_files: list[str] | None = None,
    complexity_reduction: str | None = None,
    dependencies: str | None = None,
    enables: str | None = None,
) -> Path:
    """Create a new proposal file.

    Args:
        title: Proposal title
        proposer: Proposer name/type
        category: test, refactor, feature, debt, or plan-task
        complexity: S, M, L, or XL
        summary: One-line summary
        rationale: Why this matters
        acceptance_criteria: List of acceptance criteria
        relevant_files: Optional list of relevant file paths
        complexity_reduction: Optional description of how this simplifies codebase
        dependencies: Optional description of what must happen first
        enables: Optional description of what this unblocks

    Returns:
        Path to created proposal file
    """
    proposal_id = f"PROP-{uuid4().hex[:8]}"
    filename = f"{proposal_id}.md"

    criteria_md = "\n".join(f"- [ ] {c}" for c in acceptance_criteria)

    content = f"""# Proposal: {title}

**ID:** {proposal_id}
**Proposer:** {proposer}
**Category:** {category}
**Complexity:** {complexity}
**Created:** {datetime.now().isoformat()}

## Summary
{summary}

## Rationale
{rationale}
"""

    if complexity_reduction:
        content += f"""
## Complexity Reduction
{complexity_reduction}
"""

    if dependencies:
        content += f"""
## Dependencies
{dependencies}
"""

    if enables:
        content += f"""
## Enables
{enables}
"""

    content += f"""
## Acceptance Criteria
{criteria_md}
"""

    if relevant_files:
        files_md = "\n".join(f"- {f}" for f in relevant_files)
        content += f"""
## Relevant Files
{files_md}
"""

    active_dir = get_proposal_subdir("active")
    proposal_path = active_dir / filename
    proposal_path.write_text(content)

    return proposal_path


def promote_proposal(proposal_path: Path | str, task_id: str | None = None) -> Path:
    """Promote a proposal to the task queue.

    Args:
        proposal_path: Path to the active proposal
        task_id: Optional task ID if task was already created

    Returns:
        New path in promoted directory
    """
    proposal_path = Path(proposal_path)
    promoted_dir = get_proposal_subdir("promoted")
    dest = promoted_dir / proposal_path.name

    # Append promotion info
    with open(proposal_path, "a") as f:
        f.write(f"\n**Promoted At:** {datetime.now().isoformat()}\n")
        if task_id:
            f.write(f"**Task ID:** {task_id}\n")

    os.rename(proposal_path, dest)
    return dest


def reject_proposal(proposal_path: Path | str, rejected_by: str, reason: str) -> Path:
    """Reject a proposal with feedback.

    Args:
        proposal_path: Path to the active proposal
        rejected_by: Name of the curator rejecting
        reason: Explanation of why rejected

    Returns:
        New path in rejected directory
    """
    proposal_path = Path(proposal_path)
    rejected_dir = get_proposal_subdir("rejected")
    dest = rejected_dir / proposal_path.name

    # Append rejection info
    with open(proposal_path, "a") as f:
        f.write(f"\n**Rejected By:** {rejected_by}\n")
        f.write(f"**Rejected At:** {datetime.now().isoformat()}\n")
        f.write(f"**Rejection Reason:** |\n")
        for line in reason.strip().split("\n"):
            f.write(f"  {line}\n")

    os.rename(proposal_path, dest)
    return dest


def defer_proposal(proposal_path: Path | str, reason: str | None = None) -> Path:
    """Defer a proposal for later consideration.

    Args:
        proposal_path: Path to the active proposal
        reason: Optional reason for deferral

    Returns:
        New path in deferred directory
    """
    proposal_path = Path(proposal_path)
    deferred_dir = get_proposal_subdir("deferred")
    dest = deferred_dir / proposal_path.name

    # Append deferral info
    with open(proposal_path, "a") as f:
        f.write(f"\n**Deferred At:** {datetime.now().isoformat()}\n")
        if reason:
            f.write(f"**Deferral Reason:** {reason}\n")

    os.rename(proposal_path, dest)
    return dest


def reactivate_proposal(proposal_path: Path | str) -> Path:
    """Move a deferred or rejected proposal back to active.

    Args:
        proposal_path: Path to the proposal

    Returns:
        New path in active directory
    """
    proposal_path = Path(proposal_path)
    active_dir = get_proposal_subdir("active")
    dest = active_dir / proposal_path.name

    # Append reactivation info
    with open(proposal_path, "a") as f:
        f.write(f"\n**Reactivated At:** {datetime.now().isoformat()}\n")

    os.rename(proposal_path, dest)
    return dest


def score_proposal(proposal: dict[str, Any], context: dict[str, Any] | None = None) -> float:
    """Score a proposal using curator scoring weights.

    Args:
        proposal: Proposal dictionary from parse_proposal_file
        context: Optional context with scoring factors

    Returns:
        Score between 0 and 1
    """
    weights = get_curator_scoring()
    context = context or {}

    # Get individual scores (default to 0.5 if not provided)
    priority_alignment = context.get("priority_alignment", 0.5)
    complexity_reduction = context.get("complexity_reduction", 0.5)
    risk = 1.0 - context.get("risk", 0.5)  # Invert risk (lower risk = higher score)
    dependencies_met = context.get("dependencies_met", 0.5)

    # Voice weight from proposer
    proposer = proposal.get("proposer", "unknown")
    voice_weight = get_voice_weight(proposer)
    # Normalize voice weight to 0-1 range (assuming max weight is 2.0)
    voice_score = min(voice_weight / 2.0, 1.0)

    # Calculate weighted score
    score = (
        weights["priority_alignment"] * priority_alignment
        + weights["complexity_reduction"] * complexity_reduction
        + weights["risk"] * risk
        + weights["dependencies_met"] * dependencies_met
        + weights["voice_weight"] * voice_score
    )

    return score


def detect_conflicts(proposals: list[dict[str, Any]]) -> list[tuple[dict, dict, str]]:
    """Detect conflicting proposals.

    Args:
        proposals: List of proposal dictionaries

    Returns:
        List of (proposal1, proposal2, conflict_description) tuples
    """
    conflicts = []

    # Check for proposals touching the same files
    file_proposals: dict[str, list[dict]] = {}

    for proposal in proposals:
        content = proposal.get("content", "")
        # Extract files from "Relevant Files" section
        files_match = re.search(r"## Relevant Files\n([\s\S]*?)(?=\n##|\Z)", content)
        if files_match:
            files_text = files_match.group(1)
            files = re.findall(r"^-\s*(.+)$", files_text, re.MULTILINE)
            for f in files:
                f = f.strip()
                if f not in file_proposals:
                    file_proposals[f] = []
                file_proposals[f].append(proposal)

    # Find files with multiple proposals
    for file_path, props in file_proposals.items():
        if len(props) > 1:
            for i, p1 in enumerate(props):
                for p2 in props[i + 1 :]:
                    conflict_desc = f"Both proposals modify {file_path}"
                    conflicts.append((p1, p2, conflict_desc))

    # Check for category conflicts (e.g., refactor vs feature on same area)
    # This is a simplified heuristic
    refactors = [p for p in proposals if p.get("category") == "refactor"]
    features = [p for p in proposals if p.get("category") == "feature"]

    for refactor in refactors:
        for feature in features:
            # Check if they mention similar files
            r_files = set(re.findall(r"^-\s*(.+)$", refactor.get("content", ""), re.MULTILINE))
            f_files = set(re.findall(r"^-\s*(.+)$", feature.get("content", ""), re.MULTILINE))
            overlap = r_files & f_files
            if overlap:
                conflict_desc = f"Refactor and feature proposals overlap on: {', '.join(list(overlap)[:3])}"
                conflicts.append((refactor, feature, conflict_desc))

    return conflicts


def get_proposal_status() -> dict[str, Any]:
    """Get overall proposal queue status.

    Returns:
        Dictionary with proposal counts and lists
    """
    return {
        "active": {
            "count": count_proposals("active"),
            "proposals": list_proposals("active"),
        },
        "promoted": {
            "count": count_proposals("promoted"),
            "proposals": list_proposals("promoted")[-10:],  # Last 10
        },
        "deferred": {
            "count": count_proposals("deferred"),
            "proposals": list_proposals("deferred"),
        },
        "rejected": {
            "count": count_proposals("rejected"),
            "proposals": list_proposals("rejected")[-10:],  # Last 10
        },
    }
