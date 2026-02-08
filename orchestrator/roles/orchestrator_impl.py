"""Orchestrator specialist role - implements orchestrator infrastructure changes.

This is a variant of the implementer role that:
- Claims tasks with role='orchestrator_impl' (not 'implement')
- Works on the orchestrator Python codebase (submodule)
- Commits to the submodule's main branch (not the main repo)
- Self-merges to main when tests pass; falls back to provisional queue on failure
"""

import subprocess
from pathlib import Path

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

    def _run_cmd(
        self, cmd: list[str], cwd: Path, timeout: int = 120
    ) -> subprocess.CompletedProcess:
        """Run a subprocess command, capturing output."""
        return subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout
        )

    def _parse_failed_tests(self, pytest_output: str) -> set[str]:
        """Extract the set of failed test names from pytest output.

        Looks for lines like 'FAILED tests/test_foo.py::TestBar::test_baz'
        in the short test summary section.
        """
        failed = set()
        for line in pytest_output.splitlines():
            line = line.strip()
            if line.startswith("FAILED "):
                # "FAILED tests/test_foo.py::TestBar::test_baz - AssertionError..."
                test_id = line.split(" ")[1].split(" - ")[0]
                failed.add(test_id)
        return failed

    def _try_merge_to_main(self, submodule_path: Path, task_id: str) -> bool:
        """Try to rebase, test, and fast-forward merge the agent's work to main.

        Steps:
        1. Run pytest on main to capture baseline failures
        2. Rebase orch/<task-id> onto main (in the agent's worktree submodule)
        3. Run pytest on the rebased branch — only block on NEW failures
        4. Fast-forward merge to main in the agent's worktree submodule
        5. Fetch the result into the main checkout's submodule and ff-merge

        Returns True if all steps succeed, False on any failure.
        On failure, the caller should fall back to submit_completion().
        """
        sub_branch = f"orch/{task_id}"
        main_checkout_sub = self.parent_project / "orchestrator"

        venv_python = self._find_venv_python(submodule_path)
        if not venv_python:
            self.log("Self-merge: no venv found, skipping tests")
            return False

        # Step 1: Capture baseline test failures on main
        self.log("Self-merge: running baseline pytest on main...")
        self._run_cmd(["git", "checkout", "main"], cwd=submodule_path)
        baseline_result = self._run_cmd(
            [str(venv_python), "-m", "pytest", "tests/", "-v", "--tb=short"],
            cwd=submodule_path,
            timeout=300,
        )
        baseline_failures = self._parse_failed_tests(baseline_result.stdout)
        if baseline_failures:
            self.log(f"Self-merge: {len(baseline_failures)} pre-existing failure(s) on main")
        self._run_cmd(["git", "checkout", sub_branch], cwd=submodule_path)

        # Step 2: Rebase onto main
        self.log(f"Self-merge: rebasing {sub_branch} onto main...")
        result = self._run_cmd(
            ["git", "rebase", "main"], cwd=submodule_path
        )
        if result.returncode != 0:
            self.log(f"Self-merge: rebase failed: {result.stderr.strip()}")
            self._run_cmd(["git", "rebase", "--abort"], cwd=submodule_path)
            return False

        # Step 3: Run pytest on the rebased branch
        self.log("Self-merge: running pytest on rebased branch...")
        result = self._run_cmd(
            [str(venv_python), "-m", "pytest", "tests/", "-v", "--tb=short"],
            cwd=submodule_path,
            timeout=300,
        )
        if result.returncode != 0:
            branch_failures = self._parse_failed_tests(result.stdout)
            new_failures = branch_failures - baseline_failures
            if new_failures:
                self.log(f"Self-merge: {len(new_failures)} NEW test failure(s):")
                for f in sorted(new_failures):
                    self.log(f"  - {f}")
                return False
            else:
                self.log(
                    f"Self-merge: {len(branch_failures)} failure(s) all pre-existing, proceeding"
                )
        self.log("Self-merge: tests passed")

        # Step 3: Fast-forward merge to main in agent's worktree submodule
        result = self._run_cmd(
            ["git", "checkout", "main"], cwd=submodule_path
        )
        if result.returncode != 0:
            self.log(f"Self-merge: checkout main failed: {result.stderr.strip()}")
            return False

        result = self._run_cmd(
            ["git", "merge", "--ff-only", sub_branch], cwd=submodule_path
        )
        if result.returncode != 0:
            self.log(f"Self-merge: ff-merge failed: {result.stderr.strip()}")
            # Go back to the feature branch so state is clean for fallback
            self._run_cmd(["git", "checkout", sub_branch], cwd=submodule_path)
            return False
        self.log("Self-merge: merged to main in agent worktree submodule")

        # Step 4: Propagate to main checkout's submodule
        # Fetch from agent's worktree submodule into the main checkout's submodule
        result = self._run_cmd(
            ["git", "fetch", str(submodule_path), "main"],
            cwd=main_checkout_sub,
        )
        if result.returncode != 0:
            self.log(f"Self-merge: fetch into main checkout failed: {result.stderr.strip()}")
            return False

        result = self._run_cmd(
            ["git", "merge", "--ff-only", "FETCH_HEAD"],
            cwd=main_checkout_sub,
        )
        if result.returncode != 0:
            self.log(f"Self-merge: ff-merge in main checkout failed: {result.stderr.strip()}")
            return False
        self.log("Self-merge: updated main checkout submodule")

        # Step 5: Push submodule main to origin
        result = self._run_cmd(
            ["git", "push", "origin", "main"],
            cwd=main_checkout_sub,
        )
        if result.returncode != 0:
            self.log(f"Self-merge: push failed: {result.stderr.strip()}")
            # Non-fatal — the commits are local, human can push later
            # But we still count this as a success for acceptance
            self.log("Self-merge: push failed but local merge succeeded, continuing")

        # Step 6: Update submodule ref in main repo
        main_repo = self.parent_project
        self._run_cmd(["git", "add", "orchestrator"], cwd=main_repo)
        diff = self._run_cmd(
            ["git", "diff", "--cached", "--quiet"], cwd=main_repo
        )
        if diff.returncode != 0:
            # There's a diff — commit the submodule pointer update
            result = self._run_cmd(
                ["git", "commit", "-m",
                 f"chore: update orchestrator submodule (self-merge {task_id[:8]})"],
                cwd=main_repo,
            )
            if result.returncode != 0:
                self.log(f"Self-merge: submodule ref commit failed: {result.stderr.strip()}")
                # Non-fatal — human can commit later
            else:
                # Push main repo
                push = self._run_cmd(
                    ["git", "push", "origin", "main"], cwd=main_repo
                )
                if push.returncode != 0:
                    self.log("Self-merge: main repo push failed (human can push later)")

        return True

    def _find_venv_python(self, submodule_path: Path) -> Path | None:
        """Find the venv Python executable for running tests."""
        # Check submodule's own venv first
        venv_python = submodule_path / "venv" / "bin" / "python"
        if venv_python.exists():
            return venv_python

        # Try the .orchestrator venv in the parent project
        venv_python = self.parent_project / ".orchestrator" / "venv" / "bin" / "python"
        if venv_python.exists():
            return venv_python

        return None

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
            accept_completion,
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

            # Ensure submodule is on main before creating new feature branch.
            # After a failed self-merge, the submodule may be left on a
            # previous task's branch (orch/<old-task>), which would cause
            # the new branch to include stale commits.
            self._run_cmd(["git", "checkout", "main"], cwd=submodule_path)
            self._run_cmd(
                ["git", "reset", "--hard", "origin/main"],
                cwd=submodule_path,
            )

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

            # orchestrator_impl tasks do NOT create PRs. When tests pass,
            # the agent self-merges to main. On failure, falls back to
            # the provisional queue for manual review via approve_orch.py.
            self.log("Skipping PR creation (orchestrator_impl self-merges or uses approve_orch.py)")

            result_msg = f"Implementation complete ({commits_made} submodule commits)"

            if is_db_enabled():
                if commits_made > 0:
                    merged = self._try_merge_to_main(submodule_path, task_id)
                    if merged:
                        accept_completion(
                            task_path,
                            accepted_by="self-merge",
                        )
                        self.log(f"Self-merged to main ({commits_made} submodule commits)")
                    else:
                        submit_completion(
                            task_path,
                            commits_count=commits_made,
                            turns_used=turns_used,
                        )
                        self.log(f"Self-merge failed, submitted for review ({commits_made} commits)")
                else:
                    submit_completion(
                        task_path,
                        commits_count=0,
                        turns_used=turns_used,
                    )
                    self.log("No commits, submitted for pre-check")
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
