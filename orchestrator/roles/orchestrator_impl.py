"""Orchestrator specialist role - implements orchestrator infrastructure changes.

This is a variant of the implementer role that:
- Claims tasks with role='orchestrator_impl' (not 'implement')
- Works on the orchestrator Python codebase (submodule)
- Commits to the submodule's main branch (not the main repo)
- Does NOT create PRs — approval is via approve_orch.py
"""

from .implementer import ImplementerRole
from ..queue_utils import claim_task
from .base import main_entry


class OrchestratorImplRole(ImplementerRole):
    """Specialist implementer for orchestrator infrastructure work.

    Key differences from regular ImplementerRole:
    - Creates a feature branch in the main repo for tracking, but all real
      work happens in the orchestrator/ submodule on main
    - Counts commits from the submodule, not the main repo
    - Does NOT create pull requests (approval uses approve_orch.py)
    - Provides explicit submodule paths in the agent prompt to prevent
      the agent from accidentally committing to the wrong git repo
    """

    def _create_submodule_branch(self, submodule_path, branch_name):
        """Create a feature branch in the submodule for isolated commits."""
        import subprocess
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=submodule_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _get_submodule_path(self):
        """Get the path to the orchestrator submodule in this agent's worktree.

        Returns:
            Path to the orchestrator/ submodule directory within the worktree.
            This is where the agent should make all commits.
        """
        return self.worktree / "orchestrator"

    def run(self) -> int:
        """Claim an orchestrator task and implement it.

        Overrides the parent to use role_filter='orchestrator_impl'.
        """
        # Override claim to use orchestrator_impl role filter
        task = claim_task(role_filter="orchestrator_impl", agent_name=self.agent_name)
        if not task:
            self.log("No orchestrator tasks available to claim")
            return 0

        self._claimed_task = task
        return self._run_with_task(task)

    def _run_with_task(self, task):
        """Run the orchestrator implementation flow with an already-claimed task.

        Unlike the regular implementer, this:
        1. Creates a feature branch in the main repo (for tracking only)
        2. Snapshots the submodule HEAD (not main repo HEAD)
        3. Provides explicit submodule paths in the prompt
        4. Counts commits from the submodule after Claude finishes
        5. Skips PR creation entirely
        """
        from pathlib import Path
        from ..config import is_db_enabled, get_notes_dir
        import subprocess
        from ..git_utils import (
            create_feature_branch,
            get_commit_count,
            get_head_ref,
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

        self.current_task_id = task_id
        self.log(f"Claimed orchestrator task {task_id}: {task_title}")

        # Reset tool counter for fresh turn counting
        self.reset_tool_counter()

        # The submodule path within this agent's worktree — this is where
        # ALL commits should go. The approve script looks here.
        submodule_path = self._get_submodule_path()

        try:
            # Create feature branch in main repo (for tracking/worktree purposes)
            branch_name = create_feature_branch(self.worktree, task_id, base_branch)
            self.log(f"Created branch: {branch_name}")

            # Create feature branch in the SUBMODULE so commits stay isolated
            # until approved. This prevents commits from bleeding between tasks.
            sub_branch = f"orch/{task_id}"
            self._create_submodule_branch(submodule_path, sub_branch)
            self.log(f"Created submodule branch: {sub_branch}")

            # Snapshot the SUBMODULE HEAD before implementation — this is
            # what we compare against to count commits afterward.
            # Previously this checked the main repo HEAD, which always
            # showed 0 commits since agents commit in the submodule.
            head_before = get_head_ref(submodule_path)
            self.debug_log(f"Submodule HEAD before implementation: {head_before[:8] if head_before else 'N/A'}")

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

            # Build prompt with EXPLICIT paths to prevent agents from
            # accidentally committing to the main checkout's submodule.
            # The submodule_path is an absolute path within this worktree.
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

## CRITICAL: Git Commit Location

Your worktree submodule is at: `{submodule_path}`

When committing, ALWAYS use one of these patterns:
- `git -C {submodule_path} add . && git -C {submodule_path} commit -m "..."`
- `cd {submodule_path} && git add . && git commit -m "..."`

Do NOT commit from the worktree root. Do NOT use paths like
`/Users/.../dev/boxen/orchestrator/` — that is a DIFFERENT git repo.

Verify before committing:
```bash
git -C {submodule_path} rev-parse --show-toplevel
# Must show: {submodule_path}
```

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

            # Count commits from the SUBMODULE, not the main repo.
            # This was a key bug: the old code counted main repo commits
            # (always 0) while the agent committed in the submodule.
            if head_before:
                commits_made = get_commit_count(submodule_path, since_ref=head_before)
            else:
                commits_made = get_commit_count(submodule_path)
            self.debug_log(f"Submodule commits made this session: {commits_made}")

            # Read actual tool call count (falls back to max_turns if counter missing)
            tool_count = self.read_tool_count()
            turns_used = tool_count if tool_count is not None else 200

            save_task_notes(task_id, self.agent_name, stdout, commits=commits_made, turns=turns_used)

            if stdout_log.exists():
                try:
                    stdout_log.unlink()
                except IOError:
                    pass

            if exit_code != 0:
                self.log(f"Implementation failed: {stderr}")
                fail_task(task_path, f"Claude invocation failed with exit code {exit_code}\n{stderr}")
                return exit_code

            # orchestrator_impl tasks do NOT create PRs — they commit
            # directly to the submodule's main branch. Approval
            # is handled by approve_orch.py which cherry-picks from the
            # agent's worktree submodule to the canonical main.
            self.log("Skipping PR creation (orchestrator_impl uses approve_orch.py)")

            result_msg = f"Implementation complete ({commits_made} submodule commits)"

            if is_db_enabled():
                submit_completion(
                    task_path,
                    commits_count=commits_made,
                    turns_used=turns_used,
                )
                self.log(f"Submitted for pre-check ({commits_made} submodule commits)")
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
