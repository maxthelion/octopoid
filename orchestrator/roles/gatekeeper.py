"""Gatekeeper role - reviews task branch diffs for quality.

Gatekeepers are specialized agents that review the diff between a task's
feature branch and its base branch. Each gatekeeper focuses on a specific
area (architecture, testing, QA) and records pass/fail results.

Environment variables:
    REVIEW_TASK_ID: Task ID to review
    REVIEW_CHECK_NAME: Check name (architecture, testing, qa)
"""

import os
import subprocess

from ..config import get_orchestrator_dir, is_db_enabled
from .base import main_entry
from .specialist import SpecialistRole


class GatekeeperRole(SpecialistRole):
    """Gatekeeper that reviews task branch diffs from a specialized perspective.

    Reads REVIEW_TASK_ID and REVIEW_CHECK_NAME from environment to determine
    what to review. Records results to the DB via db.record_check_result().
    """

    def __init__(self):
        super().__init__()
        self.review_task_id = os.environ.get("REVIEW_TASK_ID")
        self.check_name = os.environ.get("REVIEW_CHECK_NAME", self.focus)

    def _get_branch_diff(self, branch: str, base_branch: str) -> str | None:
        """Get the diff between a task branch and its base.

        Args:
            branch: Feature branch name
            base_branch: Base branch to diff against

        Returns:
            Diff string or None on error
        """
        try:
            # Fetch latest
            subprocess.run(
                ["git", "fetch", "origin"],
                capture_output=True,
                cwd=self.worktree,
                timeout=30,
            )

            result = subprocess.run(
                ["git", "diff", f"origin/{base_branch}...origin/{branch}"],
                capture_output=True,
                text=True,
                cwd=self.worktree,
                timeout=60,
            )

            if result.returncode != 0:
                self.log(f"git diff failed: {result.stderr}")
                return None

            return result.stdout

        except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            self.log(f"Error getting diff: {e}")
            return None

    def _get_branch_files(self, branch: str, base_branch: str) -> list[str]:
        """Get list of changed files between branches.

        Args:
            branch: Feature branch name
            base_branch: Base branch

        Returns:
            List of changed file paths
        """
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", f"origin/{base_branch}...origin/{branch}"],
                capture_output=True,
                text=True,
                cwd=self.worktree,
                timeout=30,
            )
            if result.returncode == 0:
                return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass
        return []

    def _record_check(self, status: str, summary: str) -> None:
        """Record a check result in the DB.

        Args:
            status: 'pass' or 'fail'
            summary: Brief description of the result
        """
        if is_db_enabled():
            from .. import db
            db.record_check_result(self.review_task_id, self.check_name, status, summary)
        else:
            # Fallback to filesystem for non-DB setups
            from ..review_utils import record_review_result
            record_review_result(
                self.review_task_id,
                self.check_name,
                status,
                summary,
                submitted_by=self.agent_name,
            )

    def run(self) -> int:
        """Run gatekeeper check on a task's branch diff.

        Returns:
            Exit code (0 for success)
        """
        if not self.review_task_id:
            self.log("No REVIEW_TASK_ID in environment, nothing to review")
            return 0

        self.log(f"Reviewing task {self.review_task_id}, check: {self.check_name}")

        # Load task from DB to get branch info
        branch = ""
        base_branch = "main"
        if is_db_enabled():
            from .. import db
            task = db.get_task(self.review_task_id)
            if task:
                branch = task.get("branch", "")
                # base_branch defaults to "main"
            else:
                self.log(f"No task found in DB for {self.review_task_id}")
                return 1
        else:
            # Fallback to filesystem review metadata
            from ..review_utils import load_review_meta
            meta = load_review_meta(self.review_task_id)
            if meta:
                branch = meta.get("branch", "")
                base_branch = meta.get("base_branch", "main")
            else:
                self.log(f"No review metadata found for task {self.review_task_id}")
                return 1

        if not branch:
            self.log("No branch found for task")
            self._record_check("fail", "No branch found for task")
            return 1

        # Get the diff
        diff = self._get_branch_diff(branch, base_branch)
        if not diff:
            self.log("No diff found (branch may not exist or no changes)")
            self._record_check("fail", "Could not get branch diff")
            return 1

        # Truncate large diffs
        max_diff_size = 50000
        if len(diff) > max_diff_size:
            diff = diff[:max_diff_size] + f"\n\n[... truncated {len(diff) - max_diff_size} chars ...]"

        # Get changed files list
        changed_files = self._get_branch_files(branch, base_branch)

        # Load domain-specific prompt
        domain_prompt = self.get_focus_prompt()
        focus_description = self.get_focus_description()

        # Read task file for context
        task_file_content = ""
        task_file_path = get_orchestrator_dir() / "shared" / "queue"
        # Search across queue directories for the task file
        for subdir in ["provisional", "incoming", "claimed", "done"]:
            candidate = task_file_path / subdir / f"TASK-{self.review_task_id}.md"
            if candidate.exists():
                task_file_content = candidate.read_text()
                break

        # Extract staging URL for QA checks
        staging_url = ""
        if task and self.check_name == "qa":
            staging_url = task.get("staging_url", "") or ""

        # Build the review prompt
        instructions = self.read_instructions()

        staging_section = ""
        if staging_url:
            staging_section = f"\n**Staging URL:** {staging_url}\n"

        prompt = f"""You are a gatekeeper agent performing a **{self.check_name}** review on a task implementation.

**Focus Area:** {focus_description}

{instructions}

{domain_prompt}

## Task Being Reviewed

**Task ID:** {self.review_task_id}
**Branch:** {branch} → {base_branch}
**Files Changed:** {len(changed_files)}
{staging_section}

### Task Description
{task_file_content[:3000] if task_file_content else 'No task file found.'}

### Changed Files
{chr(10).join('- ' + f for f in changed_files[:30])}

## Your Task

Review this implementation from your specialized perspective (**{self.check_name}**).

Analyze the diff carefully and determine if it passes your check.

When you've completed your review, use the /record-check skill to record your result:

```
/record-check {self.review_task_id} {self.check_name} <pass|fail> "summary" "details"
```

### Guidelines

1. **Be thorough** — Check all relevant aspects of your focus area
2. **Be specific** — Point to exact files and lines with issues
3. **Be constructive** — Explain why something is an issue and how to fix it
4. **Stay focused** — Only evaluate your specific area
5. **Be fair** — Don't reject for stylistic preferences; reject for real issues

### Recording Results

Use /record-check with:
- `status`: pass or fail
- `summary`: One-line summary of your verdict
- `details`: Full markdown report with specific file paths and line numbers

## Branch Diff

```diff
{diff}
```
"""

        # Invoke Claude with review tools
        allowed_tools = [
            "Read",
            "Glob",
            "Grep",
            "Bash",
            "Skill",
        ]

        max_turns = 15

        # QA checks need Playwright MCP and more turns for visual interaction
        if self.check_name == "qa":
            allowed_tools.append("mcp__playwright__*")
            max_turns = 30

        exit_code, stdout, stderr = self.invoke_claude(
            prompt,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
        )

        if exit_code != 0:
            self.log(f"Claude invocation failed: {stderr}")
            self._record_check("fail", f"Review agent failed to complete: {stderr[:200]}")
            return exit_code

        self.log(f"Gatekeeper check ({self.check_name}) complete for task {self.review_task_id}")
        return 0


def main():
    main_entry(GatekeeperRole)


if __name__ == "__main__":
    main()
