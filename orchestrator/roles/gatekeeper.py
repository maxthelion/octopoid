"""Gatekeeper role - specialized agents that review PRs."""

import os

from ..pr_utils import (
    get_pr_diff,
    get_pr_info,
    load_pr_meta,
    record_check_result,
)
from .base import main_entry
from .specialist import SpecialistRole


class GatekeeperRole(SpecialistRole):
    """Gatekeeper that reviews PRs from a specialized perspective.

    Gatekeepers are specialized agents with a specific focus area (lint,
    tests, architecture, style). They run checks against PRs and report
    results back to the coordinator.

    Inherits from SpecialistRole which provides:
    - focus area configuration
    - domain-specific prompt loading
    """

    def __init__(self):
        super().__init__()
        self.gatekeeper_type = self.agent_name

    def run(self) -> int:
        """Run gatekeeper check on assigned PR.

        Returns:
            Exit code (0 for success)
        """
        # Get PR number from environment (set by coordinator)
        pr_number_str = os.environ.get("PR_NUMBER")
        if not pr_number_str:
            self.log("No PR_NUMBER in environment, nothing to check")
            return 0

        try:
            pr_number = int(pr_number_str)
        except ValueError:
            self.log(f"Invalid PR_NUMBER: {pr_number_str}")
            return 1

        # Load PR metadata
        meta = load_pr_meta(pr_number)
        if not meta:
            self.log(f"No metadata found for PR-{pr_number}")
            return 1

        # Get PR info and diff
        pr_info = get_pr_info(pr_number)
        if not pr_info:
            self.log(f"Could not fetch PR-{pr_number} info")
            return 1

        pr_diff = get_pr_diff(pr_number)
        if not pr_diff:
            self.log(f"Could not fetch PR-{pr_number} diff")
            return 1

        # Mark check as running
        record_check_result(
            pr_number,
            self.focus,
            "running",
            f"Running {self.focus} check...",
        )

        # Load domain-specific prompt (from SpecialistRole)
        domain_prompt = self.get_focus_prompt()
        focus_description = self.get_focus_description()

        # Build prompt for Claude
        instructions = self.read_instructions()

        prompt = f"""You are a gatekeeper agent performing a {self.focus} check on a pull request.

**Focus Area:** {focus_description}

{instructions}

{domain_prompt}

## PR Information

**Title:** {pr_info.get('title', 'Unknown')}
**Author:** {pr_info.get('author', {}).get('login', 'unknown')}
**Branch:** {pr_info.get('headRefName', 'unknown')} → {pr_info.get('baseRefName', 'main')}
**Files Changed:** {pr_info.get('changedFiles', 0)}
**Additions:** {pr_info.get('additions', 0)}
**Deletions:** {pr_info.get('deletions', 0)}

### Description
{pr_info.get('body', 'No description provided.')}

### Changed Files
{chr(10).join('- ' + f.get('path', '') for f in pr_info.get('files', [])[:20])}

## Your Task

Review this PR from your specialized perspective ({self.focus}).

Analyze the diff and codebase to determine if the PR passes your check.

When you've completed your review, use the /record-check skill to record your result.

### Guidelines

1. **Be thorough** - Check all relevant aspects of your focus area
2. **Be specific** - Point to exact files and lines with issues
3. **Be constructive** - Explain why something is an issue and how to fix it
4. **Stay focused** - Only evaluate your specific area, not others

### Recording Results

Use /record-check with:
- `status`: passed | failed | warning
- `summary`: One-line summary
- `details`: Full markdown report (optional)
- `issues`: List of specific issues (optional)

## PR Diff

```diff
{pr_diff[:50000]}
```
"""

        # Invoke Claude with appropriate tools
        allowed_tools = [
            "Read",
            "Glob",
            "Grep",
            "Bash",  # For git/lint commands
            "Skill",  # For /record-check
        ]

        exit_code, stdout, stderr = self.invoke_claude(
            prompt,
            allowed_tools=allowed_tools,
            max_turns=15,
        )

        if exit_code != 0:
            self.log(f"Claude invocation failed: {stderr}")
            # Record failure
            record_check_result(
                pr_number,
                self.focus,
                "failed",
                f"Check failed to run: {stderr[:200]}",
            )
            return exit_code

        self.log(f"Gatekeeper check ({self.focus}) complete for PR-{pr_number}")
        return 0


def main():
    main_entry(GatekeeperRole)


if __name__ == "__main__":
    main()
