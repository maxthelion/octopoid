"""Implementer role - claims tasks and implements features."""

import json
import sys
from datetime import datetime
from pathlib import Path

from ..git_utils import (
    commit_changes,
    create_feature_branch,
    create_pull_request,
    extract_task_id_from_branch,
    get_current_branch,
    has_commits_ahead_of_base,
    has_submodule_changes,
    has_uncommitted_changes,
    push_submodule_to_main,
    stage_submodule_pointer,
)
from ..queue_utils import (
    can_claim_task,
    claim_task,
    complete_task,
    fail_task,
    find_task_by_id,
    get_continuation_tasks,
    mark_needs_continuation,
    resume_task,
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

    def _check_for_continuation_work(self) -> dict | None:
        """Check if there's continuation work to resume.

        Checks:
        1. Tasks in needs_continuation queue for this agent
        2. Uncommitted work in worktree on an agent branch

        Returns:
            Task info dict if continuation work found, None otherwise
        """
        # Check for tasks explicitly marked for continuation
        continuation_tasks = get_continuation_tasks(agent_name=self.agent_name)
        if continuation_tasks:
            return continuation_tasks[0]  # Take highest priority

        # Check if worktree has work-in-progress
        current_branch = get_current_branch(self.worktree)
        task_id = extract_task_id_from_branch(current_branch)

        if task_id:
            # We're on an agent branch - check for uncommitted work
            has_changes = has_uncommitted_changes(self.worktree)
            has_commits = has_commits_ahead_of_base(self.worktree, "main")

            if has_changes or has_commits:
                # Find the task in any queue
                task = find_task_by_id(task_id)
                if task:
                    self.log(f"Found work-in-progress for task {task_id} on branch {current_branch}")
                    task["wip_branch"] = current_branch
                    task["has_uncommitted"] = has_changes
                    task["has_commits"] = has_commits
                    return task

        return None

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

1. Review any existing work (uncommitted changes, existing commits)
2. Understand what has been done and what remains
3. Complete the implementation
4. Commit any remaining changes with clear messages
5. Create a PR when done, or summarize what you implemented

Use `git status` and `git log` to understand the current state of the work.

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

Update this whenever you start a new subtask or make significant progress (every 10-20% completion).
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
    ) -> int:
        """Handle the result of a Claude implementation session.

        Decides whether to:
        - Mark task as complete (PR created)
        - Mark task as needs_continuation (uncommitted work remains)
        - Mark task as failed (error occurred)

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
            complete_task(task_path, f"PR created: {pr_url}")
            self.clear_status()  # Task complete
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

        # Check backpressure
        can_claim, reason = can_claim_task()
        if not can_claim:
            self.log(f"Cannot claim task: {reason}")
            return 0  # Not an error, just nothing to do

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

        try:
            # Create feature branch
            branch_name = create_feature_branch(self.worktree, task_id, base_branch)
            self.log(f"Created branch: {branch_name}")

            # Build prompt for Claude
            instructions = self.read_instructions()
            task_content = task.get("content", "")

            prompt = f"""You are an implementer agent working on this task.

{instructions}

## Task Details

{task_content}

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

Update this whenever you start a new subtask or make significant progress (every 10-20% completion).
"""

            # Write initial status
            self.write_status(
                task_id=task_id,
                current_subtask="Starting implementation",
                progress_percent=5,
                task_title=task_title,
            )

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
                max_turns=50,  # Allow more turns for implementation
            )

            # Use shared result handling
            return self._handle_implementation_result(
                task_id=task_id,
                task_title=task_title,
                task_path=task_path,
                branch_name=branch_name,
                base_branch=base_branch,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )

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
