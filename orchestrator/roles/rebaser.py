"""Rebaser role - keeps task branches up to date with main.

The rebaser processes tasks that have been marked as needing rebase
(via the needs_rebase flag in the DB). For each task:

1. Finds the task's branch in the parent project repo
2. Fetches the latest main
3. Attempts to rebase the branch onto main in the dedicated rebaser worktree
4. If rebase succeeds: re-runs tests (npx vitest run), force-pushes
5. If tests pass: clears the needs_rebase flag
6. If rebase fails (conflicts): aborts, rejects task back to agent
7. If tests fail after rebase: rejects task back to agent

This role is lightweight — it runs git operations directly without
invoking Claude. It uses the dedicated rebaser worktree at
.octopoid/agents/rebaser-worktree/ (separate from the review worktree).

v1 limitations:
- Only handles regular app tasks (not orchestrator_impl / submodule tasks)
- Trivial conflict resolution is limited to what git rebase can auto-resolve
"""

import subprocess
from pathlib import Path

from ..config import get_orchestrator_dir
from .base import BaseRole, main_entry


REBASER_WORKTREE_NAME = "rebaser-worktree"


class RebaserRole(BaseRole):
    """Rebases stale task branches onto current main."""

    def _get_rebaser_worktree(self) -> Path | None:
        """Get or create the dedicated rebaser worktree.

        Returns:
            Path to the rebaser worktree, or None if creation fails
        """
        from ..scheduler import ensure_rebaser_worktree
        return ensure_rebaser_worktree()

    def run(self) -> int:
        """Process tasks needing rebase.

        Returns:
            Exit code (0 for success)
        """
        # Rebase detection is handled by the API/scheduler in v2.0
        self.log("Rebaser: no-op in API mode (rebase managed by scheduler)")
        return 0

    def _find_task_branch(self, task: dict) -> str | None:
        """Find the git branch associated with a task.

        Looks for branches matching the task ID pattern in the parent
        project repo.

        Args:
            task: Task dictionary

        Returns:
            Branch name or None if not found
        """
        task_id = task["id"]

        # Try common branch patterns
        patterns = [
            f"agent/{task_id}",
            f"feature/{task_id}",
        ]

        try:
            # Fetch latest refs
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=self.parent_project,
                capture_output=True,
                timeout=60,
            )

            # List remote branches and search for task ID
            result = subprocess.run(
                ["git", "branch", "-r", "--list", f"origin/*{task_id}*"],
                cwd=self.parent_project,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0 and result.stdout.strip():
                # Take the first matching branch
                branches = [b.strip() for b in result.stdout.strip().split("\n") if b.strip()]
                if branches:
                    # Strip the "origin/" prefix for local use
                    branch = branches[0].replace("origin/", "")
                    return branch

            # Try explicit patterns
            for pattern in patterns:
                result = subprocess.run(
                    ["git", "rev-parse", "--verify", f"origin/{pattern}"],
                    cwd=self.parent_project,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    return pattern

        except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            self.debug_log(f"Error finding branch for task {task_id}: {e}")

        return None

    def _rebase_task(self, task_id: str, branch: str) -> bool:
        """Rebase a task branch onto main and re-run tests.

        Args:
            task_id: Task identifier
            branch: Branch name to rebase

        Returns:
            True if rebase + tests succeeded
        """
        from ..queue_utils import review_reject_task

        # Use the dedicated rebaser worktree
        rebaser_worktree = self._get_rebaser_worktree()

        if not rebaser_worktree:
            self.log("Rebaser worktree not available")
            self._add_task_note(
                task_id,
                "Rebaser: dedicated worktree not available. Cannot rebase.",
            )
            return False

        try:
            # Step 1: Fetch latest
            self._run_git(rebaser_worktree, ["fetch", "origin"])

            # Step 2: Checkout the task branch
            self._run_git(rebaser_worktree, ["checkout", "-B", branch, f"origin/{branch}"])

            # Step 3: Attempt rebase onto origin/main
            rebase_ok, rebase_err = self._attempt_rebase(rebaser_worktree)

            if not rebase_ok:
                self.log(f"Rebase failed for task {task_id}: {rebase_err}")
                return False

            # Step 4: Run tests
            test_ok, test_output = self._run_tests(rebaser_worktree)

            if not test_ok:
                self.log(f"Tests failed after rebase for task {task_id}")
                return False

            # Step 5: Force-push the rebased branch
            push_ok = self._force_push(rebaser_worktree, branch)

            if not push_ok:
                self.log(f"Force-push failed for task {task_id}")
                return False

            self.log(f"Successfully rebased task {task_id}")
            return True

        except Exception as e:
            self.log(f"Unexpected error rebasing task {task_id}: {e}")
            return False

    def _attempt_rebase(self, worktree: Path) -> tuple[bool, str]:
        """Attempt to rebase the current branch onto origin/main.

        Args:
            worktree: Path to the git worktree

        Returns:
            Tuple of (success, error_message)
        """
        try:
            result = subprocess.run(
                ["git", "rebase", "origin/main"],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode == 0:
                return True, ""

            # Rebase failed — check for conflicts
            error = result.stderr + "\n" + result.stdout

            # Try to get list of conflicted files
            status_result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=U"],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=10,
            )
            conflicted = status_result.stdout.strip() if status_result.returncode == 0 else ""

            # Abort the rebase
            subprocess.run(
                ["git", "rebase", "--abort"],
                cwd=worktree,
                capture_output=True,
                timeout=30,
            )

            error_msg = error
            if conflicted:
                error_msg += f"\n\nConflicted files:\n{conflicted}"

            return False, error_msg

        except subprocess.TimeoutExpired:
            # Abort on timeout
            subprocess.run(
                ["git", "rebase", "--abort"],
                cwd=worktree,
                capture_output=True,
            )
            return False, "Rebase timed out"
        except subprocess.SubprocessError as e:
            return False, str(e)

    def _run_tests(self, worktree: Path) -> tuple[bool, str]:
        """Run the app test suite.

        Args:
            worktree: Path to the git worktree

        Returns:
            Tuple of (passed, output_text)
        """
        try:
            # Check if node_modules exists; if not, install
            if not (worktree / "node_modules").exists():
                subprocess.run(
                    ["npm", "install"],
                    cwd=worktree,
                    capture_output=True,
                    timeout=120,
                )

            result = subprocess.run(
                ["npx", "vitest", "run"],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout for tests
            )

            output = result.stdout + "\n" + result.stderr
            return result.returncode == 0, output

        except subprocess.TimeoutExpired:
            return False, "Tests timed out after 5 minutes"
        except subprocess.SubprocessError as e:
            return False, f"Failed to run tests: {e}"

    def _force_push(self, worktree: Path, branch: str) -> bool:
        """Force-push the rebased branch.

        Args:
            worktree: Path to the git worktree
            branch: Branch name to push

        Returns:
            True if push succeeded
        """
        try:
            result = subprocess.run(
                ["git", "push", "origin", branch, "--force-with-lease"],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.returncode == 0

        except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            self.debug_log(f"Force-push failed: {e}")
            return False

    def _run_git(self, worktree: Path, args: list[str]) -> subprocess.CompletedProcess:
        """Run a git command in the worktree.

        Args:
            worktree: Path to the git worktree
            args: Git command arguments (without 'git')

        Returns:
            CompletedProcess result

        Raises:
            subprocess.CalledProcessError: If command fails
        """
        return subprocess.run(
            ["git"] + args,
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )

    def _add_task_note(self, task_id: str, note: str) -> None:
        """Add a note to the task's notes file for human attention.

        Args:
            task_id: Task identifier
            note: Note text to append
        """
        from datetime import datetime

        notes_dir = self.parent_project / ".octopoid" / "shared" / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)

        notes_file = notes_dir / f"TASK-{task_id}.md"

        timestamp = datetime.now().isoformat()
        entry = f"\n\n---\n**[{timestamp}] Rebaser Note:**\n\n{note}\n"

        try:
            if notes_file.exists():
                existing = notes_file.read_text()
                notes_file.write_text(existing + entry)
            else:
                notes_file.write_text(f"# Notes for TASK-{task_id}\n{entry}")
        except OSError as e:
            self.debug_log(f"Failed to write task note: {e}")


def main():
    main_entry(RebaserRole)


if __name__ == "__main__":
    main()
