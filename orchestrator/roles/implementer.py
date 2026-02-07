"""Implementer role - claims tasks and implements features."""

from pathlib import Path

from ..config import is_db_enabled, get_notes_dir
from ..git_utils import (
    create_feature_branch,
    create_pull_request,
    get_commit_count,
    get_head_ref,
    has_uncommitted_changes,
)
from ..queue_utils import (
    claim_task,
    complete_task,
    fail_task,
    get_review_feedback,
    get_task_notes,
    save_task_notes,
    submit_completion,
)
from .base import BaseRole, main_entry


class ImplementerRole(BaseRole):
    """Implementer that claims tasks and creates pull requests."""

    def run(self) -> int:
        """Claim a task and implement it.

        Returns:
            Exit code (0 for success)
        """
        # Note: Backpressure is now checked by the scheduler before spawning.
        # This avoids wasting resources on agent startup when blocked.

        # Try to claim a task
        task = claim_task(role_filter="implement", agent_name=self.agent_name)
        if not task:
            self.log("No tasks available to claim")
            return 0

        task_id = task["id"]
        task_title = task["title"]
        base_branch = task.get("branch", "main")
        task_path = task["path"]

        self.log(f"Claimed task {task_id}: {task_title}")
        self.current_task_id = task_id

        # Reset tool counter for fresh turn counting
        self.reset_tool_counter()

        try:
            # Create feature branch
            branch_name = create_feature_branch(self.worktree, task_id, base_branch)
            self.log(f"Created branch: {branch_name}")

            # Snapshot HEAD before implementation so we count only NEW commits
            head_before = get_head_ref(self.worktree)
            self.debug_log(f"HEAD before implementation: {head_before[:8]}")

            # Build prompt for Claude
            instructions = self.read_instructions()
            task_content = task.get("content", "")

            # Set up notes file path for this task
            notes_path = get_notes_dir() / f"TASK-{task_id}.md"

            # Check for review feedback from gatekeeper rejections
            review_feedback = get_review_feedback(task_id)
            review_section = ""
            if review_feedback:
                self.log(f"Injecting review feedback (task was previously rejected by reviewers)")
                review_section = f"""
## REVIEW FEEDBACK (IMPORTANT)

This task was previously implemented but rejected by automated reviewers.
Fix the issues described below. Do NOT start from scratch — work on the
existing branch and make targeted fixes.

{review_feedback}
"""

            # Check for notes from previous attempts
            previous_notes = get_task_notes(task_id)
            notes_section = ""
            if previous_notes:
                self.log(f"Injecting notes from previous attempt(s)")
                notes_section = f"""
## Previous Agent Notes

The following notes were left by a previous agent that attempted this task.
Use these to avoid repeating the same exploration and mistakes.

{previous_notes}
"""

            prompt = f"""You are an implementer agent working on this task.

{instructions}

## Task Details

{task_content}
{review_section}{notes_section}
## Progress Notes

Write your progress and findings to this file as you work:
`{notes_path}`

Update this file regularly with:
- Key files you've identified and their purposes
- Approaches you've tried and their outcomes (including dead ends)
- What you've completed so far
- Any blockers or issues discovered
- What remains to be done
- Decisions you made and why

Good notes are valuable even when things go well — they help reviewers
understand your reasoning and help future agents working on related tasks.
Write notes after significant exploration, before attempting a complex
change, and when you discover something non-obvious about the codebase.

## Instructions

1. Analyze the codebase to understand the context
2. Implement the changes required by the task
3. Write or update tests as needed
4. Commit your changes with clear messages
5. When done, summarize what you implemented

Use the /implement skill for guidance on implementation best practices.

Remember:
- Follow existing code patterns
- Keep changes focused on the task
- Test your changes
- Create atomic, well-described commits
- Do NOT create a pull request — the orchestrator handles PR creation
"""

            # Invoke Claude with implementation tools
            # Stream stdout to a log file so output survives crashes/timeouts
            stdout_log = get_notes_dir() / f"TASK-{task_id}.stdout.log"

            allowed_tools = [
                "Read",
                "Write",
                "Edit",
                "Glob",
                "Grep",
                "Bash",
                "Skill",
            ]

            exit_code, stdout, stderr = self.invoke_claude(
                prompt,
                allowed_tools=allowed_tools,
                max_turns=100,
                stdout_log=stdout_log,
            )

            # Count commits made during this session only
            if head_before:
                commits_made = get_commit_count(self.worktree, since_ref=head_before)
            else:
                commits_made = get_commit_count(self.worktree)
            self.debug_log(f"Commits made this session: {commits_made}")

            # Append stdout tail to notes as backup (Claude may have written
            # structured notes already, this adds raw output context)
            # Read actual tool call count (falls back to max_turns if counter missing)
            tool_count = self.read_tool_count()
            turns_used = tool_count if tool_count is not None else 100

            save_task_notes(task_id, self.agent_name, stdout, commits=commits_made, turns=turns_used)

            # Clean up stdout log (notes file has the important bits)
            if stdout_log.exists():
                try:
                    stdout_log.unlink()
                except IOError:
                    pass

            if exit_code != 0:
                self.log(f"Implementation failed: {stderr}")
                fail_task(task_path, f"Claude invocation failed with exit code {exit_code}\n{stderr}")
                return exit_code

            # Check if any changes were made
            if not has_uncommitted_changes(self.worktree):
                # Changes may have been committed by Claude
                pass

            # Try to create PR
            pr_url = None
            try:
                pr_body = f"""## Summary

Automated implementation for task [{task_id}].

## Task

{task_title}

## Changes

{stdout[-2000:] if len(stdout) > 2000 else stdout}

---
Generated by orchestrator agent: {self.agent_name}
"""
                pr_url = create_pull_request(
                    self.worktree,
                    branch_name,
                    base_branch,
                    f"[{task_id}] {task_title}",
                    pr_body,
                )
                self.log(f"Created PR: {pr_url}")

            except Exception as e:
                self.log(f"Failed to create PR: {e}")
                # Continue to completion even if PR creation fails

            # Complete the task - use submit_completion in DB mode for validation
            result_msg = f"PR created: {pr_url}" if pr_url else "Implementation complete (PR creation failed)"

            if is_db_enabled():
                # Submit for validation - validator will check commits
                submit_completion(
                    task_path,
                    commits_count=commits_made,
                    turns_used=turns_used,
                )
                self.log(f"Submitted for validation ({commits_made} commits)")
            else:
                # Direct completion in file-based mode
                complete_task(task_path, result_msg)

            return 0

        except Exception as e:
            self.log(f"Task failed: {e}")
            fail_task(task_path, str(e))
            return 1


def main():
    main_entry(ImplementerRole)


if __name__ == "__main__":
    main()
