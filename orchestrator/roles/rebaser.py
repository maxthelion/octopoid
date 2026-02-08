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
.orchestrator/agents/rebaser-worktree/ (separate from the review worktree).

v1 limitations:
- Only handles regular app tasks (not orchestrator_impl / submodule tasks)
- Trivial conflict resolution is limited to what git rebase can auto-resolve
"""

import subprocess
from pathlib import Path

from ..config import get_orchestrator_dir, is_db_enabled
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
        if not is_db_enabled():
            self.log("Rebaser requires database mode to be enabled")
            return 0

        from .. import db

        tasks = db.get_tasks_needing_rebase()
        self.log(f"Found {len(tasks)} task(s) needing rebase")

        rebased = 0
        failed = 0
        skipped = 0

        for task in tasks:
            task_id = task["id"]
            role = task.get("role", "")

            # v1: Skip orchestrator_impl tasks (submodule rebasing is trickier)
            if role == "orchestrator_impl":
                self.log(f"Skipping orchestrator_impl task {task_id} (v1 limitation)")
                skipped += 1
                continue

            # Throttle: skip if rebased recently
            if db.is_rebase_throttled(task_id, cooldown_minutes=10):
                self.log(f"Skipping task {task_id} (throttled)")
                skipped += 1
                continue

            # Find the branch for this task
            branch = self._find_task_branch(task)
            if not branch:
                self.log(f"Could not find branch for task {task_id}")
                self._add_task_note(
                    task_id,
                    "Rebaser could not find branch for this task. "
                    "Ensure the task has been implemented on a feature branch.",
                )
                skipped += 1
                continue

            self.log(f"Rebasing task {task_id} (branch: {branch})")

            success = self._rebase_task(task_id, branch)
            if success:
                rebased += 1
            else:
                failed += 1

        self.log(f"Rebase complete: {rebased} rebased, {failed} failed, {skipped} skipped")
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

        Uses the dedicated rebaser worktree. On conflict or test failure,
        rejects the task back to the implementing agent with feedback.

        Args:
            task_id: Task identifier
            branch: Branch name to rebase

        Returns:
            True if rebase + tests succeeded
        """
        from .. import db
        from ..queue_utils import review_reject_task

        # Record the attempt for throttling
        db.record_rebase_attempt(task_id)

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

                feedback = (
                    f"## Rebase Conflict\n\n"
                    f"Branch `{branch}` has conflicts when rebased onto `main`.\n\n"
                    f"**Details:**\n```\n{rebase_err[:500]}\n```\n\n"
                    f"Please resolve the conflicts and push an updated branch."
                )

                # Reject task back to agent
                task = db.get_task(task_id)
                if task and task.get("file_path"):
                    review_reject_task(
                        task["file_path"],
                        feedback,
                        rejected_by="rebaser",
                    )

                return False

            # Step 4: Run tests
            test_ok, test_output = self._run_tests(rebaser_worktree)

            if not test_ok:
                self.log(f"Tests failed after rebase for task {task_id}")

                feedback = (
                    f"## Post-Rebase Test Failure\n\n"
                    f"Branch `{branch}` was rebased onto `main` successfully, "
                    f"but tests failed after rebase.\n\n"
                    f"**Test output (tail):**\n```\n{test_output[-1000:]}\n```\n\n"
                    f"The rebase may have introduced incompatibilities. "
                    f"Please fix the tests and push an updated branch."
                )

                # Reject task back to agent
                task = db.get_task(task_id)
                if task and task.get("file_path"):
                    review_reject_task(
                        task["file_path"],
                        feedback,
                        rejected_by="rebaser",
                    )

                return False

            # Step 5: Force-push the rebased branch
            push_ok = self._force_push(rebaser_worktree, branch)

            if not push_ok:
                self.log(f"Force-push failed for task {task_id}")
                self._add_task_note(
                    task_id,
                    "Rebaser: rebase succeeded and tests passed, but force-push failed. "
                    "Check remote permissions.",
                )
                return False

            # Step 6: Clear the needs_rebase flag
            db.clear_rebase_flag(task_id)
            self.log(f"Successfully rebased task {task_id}")
            return True

        except Exception as e:
            self.log(f"Unexpected error rebasing task {task_id}: {e}")
            self._add_task_note(
                task_id,
                f"Rebaser: unexpected error during rebase: {e}",
            )
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

        notes_dir = self.parent_project / ".orchestrator" / "shared" / "notes"
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
