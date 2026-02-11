"""Implementer role - claims tasks and implements features."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from ..config import is_db_enabled, get_notes_dir
from ..git_utils import (
    create_feature_branch,
    create_pull_request,
    create_task_worktree,
    extract_task_id_from_branch,
    get_commit_count,
    get_current_branch,
    get_head_ref,
    has_commits_ahead_of_base,
    has_submodule_changes,
    has_uncommitted_changes,
    push_submodule_to_main,
    stage_submodule_pointer,
)
from ..queue_utils import (
    can_claim_task,
    claim_task,
    clear_task_marker,
    complete_task,
    fail_task,
    find_task_by_id,
    get_continuation_tasks,
    get_review_feedback,
    get_task_notes,
    is_task_still_valid,
    mark_needs_continuation,
    read_task_marker,
    resume_task,
    save_task_notes,
    submit_completion,
    write_task_marker,
)
from .base import BaseRole, main_entry


class ImplementerRole(BaseRole):
    """Implementer that claims tasks and creates pull requests."""

    def _get_status_file_path(self) -> Path:
        """Get the path to this agent's status file."""
        # Status files go in .orchestrator/agents/{agent_name}/status.json
        orchestrator_root = Path(__file__).parent.parent.parent.parent / ".orchestrator"
        status_dir = orchestrator_root / "agents" / self.agent_name
        status_dir.mkdir(parents=True, exist_ok=True)
        return status_dir / "status.json"

    def write_status(
        self,
        task_id: str,
        current_subtask: str,
        progress_percent: int,
        task_title: str = "",
    ) -> None:
        """Write current status to the agent's status file.

        Args:
            task_id: The task being worked on
            current_subtask: Description of current work
            progress_percent: Estimated completion (0-100)
            task_title: Optional task title for display
        """
        status = {
            "task_id": task_id,
            "task_title": task_title,
            "current_subtask": current_subtask,
            "progress_percent": min(100, max(0, progress_percent)),
            "last_updated": datetime.now().isoformat(),
            "agent_name": self.agent_name,
        }
        status_path = self._get_status_file_path()
        with open(status_path, "w") as f:
            json.dump(status, f, indent=2)

    def clear_status(self) -> None:
        """Clear the status file when task is complete or failed."""
        status_path = self._get_status_file_path()
        if status_path.exists():
            status_path.unlink()

    @staticmethod
    def _store_pr_in_db(task_id: str, pr_url: str) -> None:
        """Extract PR number from URL and store both in the DB.

        Best-effort: logs but does not raise on failure.
        """
        try:
            if not is_db_enabled():
                return
            match = re.search(r"/pull/(\d+)", pr_url)
            if not match:
                return
            pr_number = int(match.group(1))
            from .. import db
            db.update_task(task_id, pr_number=pr_number, pr_url=pr_url)
        except Exception:
            pass  # Best-effort — don't break PR flow

    def _check_for_continuation_work(self) -> dict | None:
        """Check if there's continuation work to resume.

        Checks:
        1. Task marker file in worktree (most reliable)
        2. Tasks in needs_continuation queue for this agent
        3. Uncommitted work in worktree on an agent branch (with validation)

        Returns:
            Task info dict if continuation work found, None otherwise
        """
        # Check task marker file first - this is the source of truth
        marker = read_task_marker()
        if marker:
            task_id = marker.get("task_id")
            # Validate the task is still active (not in done/failed)
            if task_id and is_task_still_valid(task_id):
                task = find_task_by_id(task_id, subdirs=["claimed", "needs_continuation"])
                if task:
                    self.log(f"Found task marker for {task_id} - resuming")
                    task["wip_branch"] = get_current_branch(self.worktree)
                    task["has_uncommitted"] = has_uncommitted_changes(self.worktree)
                    task["has_commits"] = has_commits_ahead_of_base(self.worktree, "main")
                    return task
            else:
                # Task marker exists but task is done/failed - clean up
                self.log(f"Task {task_id} is no longer active - clearing marker and resetting worktree")
                clear_task_marker()
                self._reset_worktree()
                return None

        # Check for tasks explicitly marked for continuation
        continuation_tasks = get_continuation_tasks(agent_name=self.agent_name)
        if continuation_tasks:
            return continuation_tasks[0]  # Take highest priority

        # Check if worktree has work-in-progress on an agent branch
        current_branch = get_current_branch(self.worktree)
        task_id = extract_task_id_from_branch(current_branch)

        if task_id:
            # Validate the task is still active before resuming
            if not is_task_still_valid(task_id):
                self.log(f"Task {task_id} from branch is no longer active - resetting worktree")
                self._reset_worktree()
                return None

            # We're on an agent branch - check for uncommitted work
            has_changes = has_uncommitted_changes(self.worktree)
            has_commits = has_commits_ahead_of_base(self.worktree, "main")

            if has_changes or has_commits:
                # Find the task in active queues only
                task = find_task_by_id(task_id, subdirs=["claimed", "needs_continuation"])
                if task:
                    self.log(f"Found work-in-progress for task {task_id} on branch {current_branch}")
                    task["wip_branch"] = current_branch
                    task["has_uncommitted"] = has_changes
                    task["has_commits"] = has_commits
                    return task

        return None

    def _reset_worktree(self) -> None:
        """Reset worktree to clean state on main branch."""
        import subprocess

        self.log("Resetting worktree to main")
        try:
            # Detach HEAD first to allow checking out main
            subprocess.run(
                ["git", "checkout", "--detach", "HEAD"],
                cwd=self.worktree,
                capture_output=True,
                check=False,
            )
            # Clean untracked files
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=self.worktree,
                capture_output=True,
                check=False,
            )
            # Discard any changes
            subprocess.run(
                ["git", "checkout", "."],
                cwd=self.worktree,
                capture_output=True,
                check=False,
            )
            # Fetch latest
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=self.worktree,
                capture_output=True,
                check=False,
            )
            # Reset to origin/main
            subprocess.run(
                ["git", "reset", "--hard", "origin/main"],
                cwd=self.worktree,
                capture_output=True,
                check=True,
            )
            self.log("Worktree reset complete")
        except subprocess.CalledProcessError as e:
            self.log(f"Warning: worktree reset failed: {e}")

    def _resume_task(self, task: dict) -> int:
        """Resume a task that was previously interrupted.

        Args:
            task: Task info dict (may have wip_branch, has_uncommitted, has_commits)

        Returns:
            Exit code (0 for success)
        """
        task_id = task["id"]
        task_title = task["title"]
        base_branch = task.get("branch", "main")
        task_path = task["path"]
        wip_branch = task.get("wip_branch")

        self.log(f"Resuming task {task_id}: {task_title}")

        # Write initial status
        self.write_status(
            task_id=task_id,
            current_subtask="Resuming previous work",
            progress_percent=10,
            task_title=task_title,
        )

        # If task is in needs_continuation, move it back to claimed
        if "needs_continuation" in str(task_path):
            task_path = resume_task(task_path, agent_name=self.agent_name)
            task["path"] = task_path

        # Write/update task marker
        write_task_marker(task_id, task_path)

        try:
            # Build prompt for continuation
            instructions = self.read_instructions()
            task_content = task.get("content", "")

            has_uncommitted = task.get("has_uncommitted", False)
            has_commits = task.get("has_commits", False)

            continuation_context = ""
            if has_uncommitted:
                continuation_context += "\n**Note:** There are uncommitted changes in the worktree from a previous session."
            if has_commits:
                continuation_context += f"\n**Note:** There are commits on branch `{wip_branch}` that haven't been pushed/PR'd yet."

            prompt = f"""You are an implementer agent RESUMING work on this task.

{instructions}

## Task Details

{task_content}

## Continuation Context

This task was previously started but not completed. You are picking up where the previous session left off.
{continuation_context}

## Instructions

1. **First, read the existing plan** at `.orchestrator/agents/{self.agent_name}/plan.md`
   - Review the approach and steps
   - Check the progress log to see what was done
   - Identify which steps are already checked off
2. Review any existing work (uncommitted changes, existing commits) with `git status` and `git log`
3. **Continue from the next unchecked step** in the plan
4. As you complete steps:
   - Check them off in the plan with `[x]`
   - Add progress log entries
   - Update status.json with progress_percent based on completed/total steps
5. Commit any remaining changes with clear messages
6. Create a PR when done, or summarize what you implemented

**If no plan.md exists**: Create one before continuing, documenting what work has already been done (checked off) based on your review of existing changes.

Remember:
- Follow existing code patterns
- Keep changes focused on the task
- Test your changes
- Create atomic, well-described commits

## Status Updates

Periodically update your status file so the dashboard shows your progress.
Write to `.orchestrator/agents/{self.agent_name}/status.json`:

```bash
cat > .orchestrator/agents/{self.agent_name}/status.json << 'EOF'
{{
  "task_id": "{task_id}",
  "task_title": "{task_title}",
  "current_subtask": "Description of what you're doing now",
  "progress_percent": 50,
  "last_updated": "$(date -Iseconds)"
}}
EOF
```

Update this whenever you complete a step in your plan. Calculate progress_percent from (completed steps / total steps) * 100.
"""

            # Invoke Claude with implementation tools
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
                max_turns=50,
            )

            # Handle the result
            return self._handle_implementation_result(
                task_id=task_id,
                task_title=task_title,
                task_path=task_path,
                branch_name=wip_branch or get_current_branch(self.worktree),
                base_branch=base_branch,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                skip_pr=task.get("skip_pr", False),
            )

        except Exception as e:
            self.log(f"Task resumption failed: {e}")
            fail_task(task_path, str(e))
            return 1

    def _handle_implementation_result(
        self,
        task_id: str,
        task_title: str,
        task_path,
        branch_name: str,
        base_branch: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        skip_pr: bool = False,
    ) -> int:
        """Handle the result of a Claude implementation session.

        Decides whether to:
        - Mark task as complete (PR created, or merged directly if skip_pr)
        - Mark task as needs_continuation (uncommitted work remains)
        - Mark task as failed (error occurred)

        Args:
            skip_pr: If True, skip PR creation and merge directly to main

        Returns:
            Exit code
        """
        if exit_code != 0:
            self.log(f"Implementation failed: {stderr}")
            # Check if there's partial work to preserve
            if has_uncommitted_changes(self.worktree) or has_commits_ahead_of_base(self.worktree, base_branch):
                self.log("Partial work detected - marking for continuation")
                mark_needs_continuation(
                    task_path,
                    reason=f"claude_error_exit_{exit_code}",
                    branch_name=branch_name,
                    agent_name=self.agent_name,
                )
            else:
                fail_task(task_path, f"Claude invocation failed with exit code {exit_code}\n{stderr}")
            return exit_code

        # Check for uncommitted changes after Claude exits
        has_uncommitted = has_uncommitted_changes(self.worktree)
        has_commits = has_commits_ahead_of_base(self.worktree, base_branch)

        if not has_commits and not has_uncommitted:
            # No work was done
            self.log("No changes made - task may need different approach")
            fail_task(task_path, "Claude completed without making any changes")
            self.clear_status()  # Task failed
            clear_task_marker()  # Clear marker so we don't resume
            self._reset_worktree()  # Reset for next task
            return 0

        # Handle submodule changes before creating PR
        # Submodule commits must be pushed to the submodule remote first,
        # otherwise the boxen PR will reference commits that don't exist remotely
        if has_submodule_changes(self.worktree, "orchestrator"):
            self.log("Detected orchestrator submodule changes - pushing to submodule main")
            success, msg = push_submodule_to_main(
                self.worktree,
                "orchestrator",
                commit_message=f"[{task_id}] {task_title}",
            )
            if success:
                self.log(f"Submodule push: {msg}")
                # Stage the submodule pointer update in the parent repo
                stage_submodule_pointer(self.worktree, "orchestrator")
                # Commit the submodule pointer change
                commit_changes(self.worktree, f"Update orchestrator submodule for [{task_id}]")
            else:
                self.log(f"Warning: Failed to push submodule changes: {msg}")
                # Continue anyway - the PR might fail but we'll log the issue

        # Handle completion: either skip PR and merge directly, or create PR
        if skip_pr:
            # Skip PR creation - merge directly to base branch
            try:
                import subprocess

                # Checkout base branch and merge the feature branch
                subprocess.run(
                    ["git", "checkout", base_branch],
                    cwd=self.worktree,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "merge", branch_name, "--no-ff", "-m", f"[{task_id}] {task_title}"],
                    cwd=self.worktree,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "push", "origin", base_branch],
                    cwd=self.worktree,
                    check=True,
                    capture_output=True,
                )
                self.log(f"Merged directly to {base_branch} (skip_pr=true)")
                complete_task(task_path, f"Merged directly to {base_branch}")
                self.clear_status()
                clear_task_marker()
                self._reset_worktree()
                return 0
            except subprocess.CalledProcessError as e:
                self.log(f"Failed to merge directly: {e}")
                mark_needs_continuation(
                    task_path,
                    reason=f"direct_merge_failed: {e}",
                    branch_name=branch_name,
                    agent_name=self.agent_name,
                )
                return 0

        # Try to create PR
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
            self._store_pr_in_db(task_id, pr_url)
            complete_task(task_path, f"PR created: {pr_url}")
            self.clear_status()  # Task complete
            clear_task_marker()  # Clear marker
            self._reset_worktree()  # Reset for next task
            return 0

        except Exception as e:
            self.log(f"Failed to create PR: {e}")
            # Check why PR creation failed
            if has_uncommitted:
                # Work exists but wasn't committed - needs continuation
                self.log("Uncommitted changes remain - marking for continuation")
                mark_needs_continuation(
                    task_path,
                    reason="uncommitted_changes",
                    branch_name=branch_name,
                    agent_name=self.agent_name,
                )
            elif has_commits:
                # Commits exist but PR failed - might be push issue
                self.log("Commits exist but PR failed - marking for continuation")
                mark_needs_continuation(
                    task_path,
                    reason=f"pr_creation_failed: {e}",
                    branch_name=branch_name,
                    agent_name=self.agent_name,
                )
            else:
                complete_task(task_path, f"Implementation attempted but PR creation failed: {e}")

            return 0

    def run(self) -> int:
        """Claim a task and implement it.

        Returns:
            Exit code (0 for success)
        """
        # First check for continuation work
        continuation_task = self._check_for_continuation_work()
        if continuation_task:
            return self._resume_task(continuation_task)

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

        # Create ephemeral task worktree
        self.log("Creating ephemeral task worktree...")
        task_worktree = create_task_worktree(task)
        self.log(f"Task worktree created at: {task_worktree}")

        # Switch to task worktree for all subsequent operations
        self.worktree = task_worktree

        # Write task marker to link worktree state to this task
        write_task_marker(task_id, task_path)

        try:
            # Feature branch is already created by create_task_worktree
            # Just get the current branch name
            branch_name = get_current_branch(self.worktree)
            self.log(f"Working on branch: {branch_name}")

            # Snapshot HEAD before implementation so we count only NEW commits
            head_before = get_head_ref(self.worktree)
            self.debug_log(f"HEAD before implementation: {head_before[:8]}")

            # Build prompt for Claude
            instructions = self.read_instructions()
            task_content = task.get("content", "")

            # Set up notes file path for this task
            notes_path = get_notes_dir() / f"TASK-{task_id}.md"

            # Build rejection banner if task was previously rejected
            rejection_count = task.get("rejection_count", 0)
            attempt_count = task.get("attempt_count", 0)
            rejection_banner = ""
            if rejection_count > 0 or attempt_count > 0:
                self.log(f"Task was previously attempted (attempts={attempt_count}, rejections={rejection_count})")
                rejection_banner = f"""
## ⚠️ PREVIOUS ATTEMPT REJECTED ⚠️

This task has been attempted before and the previous submission was REJECTED.
- **Previous attempts:** {attempt_count}
- **Review rejections:** {rejection_count}

The existing code on the branch is INSUFFICIENT. You MUST read the task
file carefully, review the rejection feedback below, and make real changes.
Do NOT submit without committing new code. A 0-commit submission will be
automatically rejected.

"""

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
{rejection_banner}
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

## Before Starting: Check if Work is Already Done

FIRST, review the acceptance criteria in the task. Check whether each criterion
is already satisfied by the existing code. If ALL criteria are already met:
1. Write to your notes file: "ALREADY_DONE: All acceptance criteria are met by existing code."
2. List which criteria you checked and how they're satisfied.
3. Stop immediately — do not make any commits or changes.

This check should take no more than 3-5 tool calls.

## Instructions

1. **First, create a plan document** at `.orchestrator/agents/{self.agent_name}/plan.md`:
   - Describe your high-level approach
   - Break the task into checkable steps (typically 5-10)
   - List files you expect to modify
   - Start a progress log

2. Analyze the codebase to understand the context
3. Implement the changes required by the task
4. Write or update tests as needed
5. Commit your changes with clear messages
6. When done, summarize what you implemented

As you complete steps, check them off in plan.md with `[x]` and add progress log entries.

Use the /implement skill for guidance on implementation best practices.

Remember:
- Follow existing code patterns
- Keep changes focused on the task
- Test your changes
- Create atomic, well-described commits
- Do NOT create a pull request — the orchestrator handles PR creation
"""

            # Write initial status
            self.write_status(
                task_id=task_id,
                current_subtask="Starting implementation",
                progress_percent=5,
                task_title=task_title,
            )

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
                self._store_pr_in_db(task_id, pr_url)

            except Exception as e:
                self.log(f"Failed to create PR: {e}")
                # Continue to completion even if PR creation fails

            # Complete the task - use submit_completion in DB mode for pre-check
            result_msg = f"PR created: {pr_url}" if pr_url else "Implementation complete (PR creation failed)"

            if is_db_enabled():
                # Submit for pre-check - scheduler will check commits
                submit_completion(
                    task_path,
                    commits_count=commits_made,
                    turns_used=turns_used,
                )
                self.log(f"Submitted for pre-check ({commits_made} commits)")
            else:
                # Direct completion in file-based mode
                complete_task(task_path, result_msg)

            return 0

        except Exception as e:
            self.log(f"Task failed: {e}")
            # Check for partial work even on exceptions
            if has_uncommitted_changes(self.worktree) or has_commits_ahead_of_base(self.worktree, base_branch):
                self.log("Partial work detected - marking for continuation")
                mark_needs_continuation(
                    task_path,
                    reason=f"exception: {e}",
                    branch_name=branch_name,
                    agent_name=self.agent_name,
                )
                # Leave status file to show what was in progress
            else:
                fail_task(task_path, str(e))
                self.clear_status()  # Task failed completely
            return 1


def main():
    main_entry(ImplementerRole)


if __name__ == "__main__":
    main()
