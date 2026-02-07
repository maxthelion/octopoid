"""Orchestrator specialist role - implements orchestrator infrastructure changes.

This is a variant of the implementer role that:
- Claims tasks with role='orchestrator_impl' (not 'implement')
- Works on the orchestrator Python codebase (submodule)
- Runs orchestrator-specific tests
- Has different constraints and domain knowledge
"""

from .implementer import ImplementerRole
from ..queue_utils import claim_task
from .base import main_entry


class OrchestratorImplRole(ImplementerRole):
    """Specialist implementer for orchestrator infrastructure work."""

    def run(self) -> int:
        """Claim an orchestrator task and implement it.

        Overrides the parent to use role_filter='orchestrator_impl'.
        The rest of the implementation flow (branch, Claude, PR, submit) is identical.
        """
        # Override claim to use orchestrator_impl role filter
        task = claim_task(role_filter="orchestrator_impl", agent_name=self.agent_name)
        if not task:
            self.log("No orchestrator tasks available to claim")
            return 0

        # Inject the task back and delegate to parent's flow
        # We do this by temporarily patching claim_task to return our task
        self._claimed_task = task
        return self._run_with_task(task)

    def _run_with_task(self, task):
        """Run the implementer flow with an already-claimed task."""
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
            complete_task,
            fail_task,
            get_task_notes,
            save_task_notes,
            submit_completion,
        )

        task_id = task["id"]
        task_title = task["title"]
        base_branch = task.get("branch", "main")
        task_path = task["path"]

        self.log(f"Claimed orchestrator task {task_id}: {task_title}")

        try:
            branch_name = create_feature_branch(self.worktree, task_id, base_branch)
            self.log(f"Created branch: {branch_name}")

            head_before = get_head_ref(self.worktree)
            self.debug_log(f"HEAD before implementation: {head_before[:8]}")

            instructions = self.read_instructions()
            task_content = task.get("content", "")

            notes_path = get_notes_dir() / f"TASK-{task_id}.md"

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

            prompt = f"""You are an orchestrator specialist agent. You work on the orchestrator
infrastructure code (Python), NOT the Boxen application code (React/TypeScript).

{instructions}

## Task Details

{task_content}
{notes_section}
## Progress Notes

Write your progress and findings to this file as you work:
`{notes_path}`

## Orchestrator-Specific Instructions

1. All code changes go in the `orchestrator/` submodule (Python)
2. Run tests with: `cd orchestrator && ./venv/bin/python -m pytest tests/ -v`
3. Do NOT run `pip install -e .` — it will corrupt the shared scheduler venv
4. The orchestrator venv is at `.orchestrator/venv/` or `orchestrator/venv/`
5. Key files: `orchestrator/orchestrator/db.py`, `queue_utils.py`, `scheduler.py`
6. The DB is SQLite — schema changes need migrations in `db.py`
7. Commit in the submodule directory, not the main repo root

## General Instructions

1. Analyze the orchestrator codebase to understand the context
2. Implement the changes required by the task
3. Write or update tests as needed
4. Commit your changes with clear messages
5. When done, summarize what you implemented

Remember:
- Follow existing orchestrator code patterns
- Keep changes focused on the task
- Test your changes with the orchestrator test suite
- Create atomic, well-described commits
- Do NOT create a pull request — the orchestrator handles PR creation
"""

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
                max_turns=200,
                stdout_log=stdout_log,
            )

            if head_before:
                commits_made = get_commit_count(self.worktree, since_ref=head_before)
            else:
                commits_made = get_commit_count(self.worktree)
            self.debug_log(f"Commits made this session: {commits_made}")

            save_task_notes(task_id, self.agent_name, stdout, commits=commits_made, turns=200)

            if stdout_log.exists():
                try:
                    stdout_log.unlink()
                except IOError:
                    pass

            if exit_code != 0:
                self.log(f"Implementation failed: {stderr}")
                fail_task(task_path, f"Claude invocation failed with exit code {exit_code}\n{stderr}")
                return exit_code

            pr_url = None
            try:
                pr_body = f"""## Summary

Automated implementation for orchestrator task [{task_id}].

## Task

{task_title}

## Changes

{stdout[-2000:] if len(stdout) > 2000 else stdout}

---
Generated by orchestrator specialist: {self.agent_name}
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

            result_msg = f"PR created: {pr_url}" if pr_url else "Implementation complete (PR creation failed)"

            if is_db_enabled():
                submit_completion(
                    task_path,
                    commits_count=commits_made,
                    turns_used=200,
                )
                self.log(f"Submitted for validation ({commits_made} commits)")
            else:
                complete_task(task_path, result_msg)

            return 0

        except Exception as e:
            self.log(f"Task failed: {e}")
            fail_task(task_path, str(e))
            return 1


def main():
    main_entry(OrchestratorImplRole)


if __name__ == "__main__":
    main()
