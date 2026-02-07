"""Check runner role - runs automated checks on provisional tasks.

The check runner processes provisional tasks that have pending automated
checks (e.g. pytest-submodule, gk-testing-octopoid). For each task with
pending checks:

1. Finds the agent's worktree and identifies commits to test
2. Sets up a clean test environment in the review worktree
3. Runs the appropriate check (e.g. pytest)
4. Records pass/fail in the DB's check_results field
5. On failure: rejects the task back to the agent with test output

This role is lightweight — it runs checks directly without invoking Claude.

The gk-testing-octopoid check is the rebase + pytest gatekeeper for
orchestrator (Octopoid) tasks. It:
- Rebases agent commits onto current origin/sqlite-model instead of cherry-picking
- Provides richer failure context (conflict details, test failure summaries)
- Identifies whether failures are likely caused by the agent's changes vs pre-existing
"""

import os
import subprocess
from pathlib import Path

from ..config import get_orchestrator_dir, is_db_enabled
from ..queue_utils import list_tasks, review_reject_task
from .base import BaseRole, main_entry


# Valid check types that the check runner knows how to handle
VALID_CHECK_TYPES = {"pytest-submodule", "gk-testing-octopoid"}


class CheckRunnerRole(BaseRole):
    """Runs automated checks on provisional tasks."""

    def run(self) -> int:
        """Process provisional tasks with pending checks.

        Returns:
            Exit code (0 for success)
        """
        if not is_db_enabled():
            self.log("Check runner requires database mode to be enabled")
            return 0

        from .. import db

        provisional_tasks = list_tasks("provisional")
        self.log(f"Found {len(provisional_tasks)} provisional tasks")

        checked = 0
        for task in provisional_tasks:
            task_id = task["id"]
            checks = task.get("checks", [])
            check_results = task.get("check_results", {})

            if not checks:
                continue

            # Find checks that haven't been run yet
            pending_checks = [
                c for c in checks
                if c not in check_results or check_results[c].get("status") not in ("pass", "fail")
            ]

            if not pending_checks:
                continue

            self.log(f"Task {task_id}: pending checks = {pending_checks}")

            for check_name in pending_checks:
                if check_name == "pytest-submodule":
                    self._run_pytest_submodule(task_id, task)
                    checked += 1
                elif check_name == "gk-testing-octopoid":
                    self._run_gk_testing(task_id, task)
                    checked += 1
                else:
                    self.log(f"Unknown check type: {check_name}, skipping")

        # After running checks, process results: reject failed tasks
        self._process_check_results()

        if checked:
            self.log(f"Ran {checked} checks")
        return 0

    def _run_pytest_submodule(self, task_id: str, task: dict) -> None:
        """Run pytest in the orchestrator submodule for a task.

        Strategy:
        1. Find the agent's worktree (from claimed_by)
        2. Use the review worktree to test in isolation
        3. Cherry-pick the agent's submodule commits
        4. Run pytest
        5. Record result

        Args:
            task_id: Task identifier
            task: Task dictionary
        """
        from .. import db

        claimed_by = task.get("claimed_by")
        parent_project = self.parent_project

        # Find the agent's submodule commits
        agent_worktree = self._find_agent_worktree(claimed_by, task_id)
        if not agent_worktree:
            self.log(f"Could not find worktree for task {task_id}")
            db.record_check_result(
                task_id, "pytest-submodule", "fail",
                "Could not find agent worktree to test commits"
            )
            return

        submodule_path = agent_worktree / "orchestrator"
        if not submodule_path.exists():
            self.log(f"No orchestrator submodule in {agent_worktree}")
            db.record_check_result(
                task_id, "pytest-submodule", "fail",
                "No orchestrator submodule found in agent worktree"
            )
            return

        # Get the agent's commits on sqlite-model
        commits = self._get_submodule_commits(submodule_path)
        if not commits:
            self.log(f"No submodule commits found for task {task_id}")
            db.record_check_result(
                task_id, "pytest-submodule", "fail",
                "No commits found in orchestrator submodule"
            )
            return

        self.log(f"Found {len(commits)} commit(s) to test for task {task_id}")

        # Use the review worktree's orchestrator submodule for testing
        review_worktree = parent_project / ".orchestrator" / "agents" / "review-worktree"
        review_submodule = review_worktree / "orchestrator"

        if not review_submodule.exists():
            self.log(f"Review worktree submodule not found at {review_submodule}")
            db.record_check_result(
                task_id, "pytest-submodule", "fail",
                "Review worktree orchestrator submodule not found"
            )
            return

        # Set up clean state: fetch and reset to origin/sqlite-model
        setup_ok, setup_err = self._setup_clean_submodule(review_submodule, submodule_path)
        if not setup_ok:
            db.record_check_result(
                task_id, "pytest-submodule", "fail",
                f"Failed to set up test environment: {setup_err}"
            )
            return

        # Cherry-pick commits
        cherry_ok, cherry_err = self._cherry_pick_commits(review_submodule, commits)
        if not cherry_ok:
            # Abort cherry-pick and record failure
            subprocess.run(
                ["git", "cherry-pick", "--abort"],
                cwd=review_submodule,
                capture_output=True,
            )
            db.record_check_result(
                task_id, "pytest-submodule", "fail",
                f"Cherry-pick failed: {cherry_err}"
            )
            return

        # Run pytest
        test_ok, test_output = self._run_pytest(review_submodule)

        if test_ok:
            db.record_check_result(
                task_id, "pytest-submodule", "pass",
                f"All tests passed ({len(commits)} commit(s) tested)"
            )
            self.log(f"pytest-submodule PASSED for task {task_id}")
        else:
            # Truncate output for summary
            summary = test_output[-500:] if len(test_output) > 500 else test_output
            db.record_check_result(
                task_id, "pytest-submodule", "fail",
                f"Tests failed:\n```\n{summary}\n```"
            )
            self.log(f"pytest-submodule FAILED for task {task_id}")

    def _run_gk_testing(self, task_id: str, task: dict) -> None:
        """Run the gk-testing-octopoid check: rebase onto current sqlite-model + pytest.

        Unlike pytest-submodule which cherry-picks, this check rebases the
        agent's commits onto the current origin/sqlite-model. This catches
        divergence issues and produces a cleaner history.

        Flow:
        1. Find agent worktree and submodule commits
        2. Set up review worktree with clean origin/sqlite-model
        3. Create a temporary branch and rebase agent commits onto it
        4. Run pytest on the rebased code
        5. Record pass/fail with context

        Args:
            task_id: Task identifier
            task: Task dictionary
        """
        from .. import db

        claimed_by = task.get("claimed_by")

        # Find the agent's submodule commits
        agent_worktree = self._find_agent_worktree(claimed_by, task_id)
        if not agent_worktree:
            self.log(f"Could not find worktree for task {task_id}")
            db.record_check_result(
                task_id, "gk-testing-octopoid", "fail",
                "Could not find agent worktree to test commits"
            )
            return

        submodule_path = agent_worktree / "orchestrator"
        if not submodule_path.exists():
            self.log(f"No orchestrator submodule in {agent_worktree}")
            db.record_check_result(
                task_id, "gk-testing-octopoid", "fail",
                "No orchestrator submodule found in agent worktree"
            )
            return

        # Get the agent's commits on sqlite-model
        commits = self._get_submodule_commits(submodule_path)
        if not commits:
            self.log(f"No submodule commits found for task {task_id}")
            db.record_check_result(
                task_id, "gk-testing-octopoid", "fail",
                "No commits found in orchestrator submodule"
            )
            return

        self.log(f"Found {len(commits)} commit(s) to test for task {task_id}")

        # Use the review worktree's orchestrator submodule for testing
        review_worktree = self.parent_project / ".orchestrator" / "agents" / "review-worktree"
        review_submodule = review_worktree / "orchestrator"

        if not review_submodule.exists():
            self.log(f"Review worktree submodule not found at {review_submodule}")
            db.record_check_result(
                task_id, "gk-testing-octopoid", "fail",
                "Review worktree orchestrator submodule not found"
            )
            return

        # Set up clean state: fetch and reset to origin/sqlite-model
        setup_ok, setup_err = self._setup_clean_submodule(review_submodule, submodule_path)
        if not setup_ok:
            db.record_check_result(
                task_id, "gk-testing-octopoid", "fail",
                f"Failed to set up test environment: {setup_err}"
            )
            return

        # Check if base has diverged since agent forked
        divergence_info = self._check_divergence(review_submodule, commits)

        # Rebase commits onto current origin/sqlite-model
        rebase_ok, rebase_err = self._rebase_commits(review_submodule, commits)
        if not rebase_ok:
            # Build a helpful rejection message with divergence context
            summary_parts = ["## Rebase Failed\n"]
            summary_parts.append(f"Could not rebase {len(commits)} commit(s) "
                                 f"onto current `origin/sqlite-model`.\n")
            if divergence_info:
                summary_parts.append(f"\n### What changed on sqlite-model\n\n{divergence_info}\n")
            summary_parts.append(f"\n### Conflict details\n\n{rebase_err}\n")
            summary_parts.append("\n### Suggested fix\n\n"
                                 "Pull the latest `origin/sqlite-model`, rebase your commits, "
                                 "resolve conflicts, and resubmit.")

            db.record_check_result(
                task_id, "gk-testing-octopoid", "fail",
                "\n".join(summary_parts)
            )
            self.log(f"gk-testing-octopoid FAILED for task {task_id}: rebase conflict")
            return

        # Run pytest on the rebased code
        test_ok, test_output = self._run_pytest(review_submodule)

        if test_ok:
            summary = f"All tests passed ({len(commits)} commit(s) rebased and tested)"
            if divergence_info:
                summary += f"\n\nNote: Base branch had moved. Rebase succeeded automatically."
            db.record_check_result(task_id, "gk-testing-octopoid", "pass", summary)
            self.log(f"gk-testing-octopoid PASSED for task {task_id}")
        else:
            # Build a detailed failure summary
            summary_parts = ["## Test Failures After Rebase\n"]
            summary_parts.append(f"Rebased {len(commits)} commit(s) onto current "
                                 f"`origin/sqlite-model` successfully, but tests failed.\n")

            if divergence_info:
                summary_parts.append(f"\n### Base branch changes\n\n{divergence_info}\n")
                summary_parts.append("*Note: Failures may be caused by conflicts with "
                                     "upstream changes rather than your code.*\n")

            # Include the tail of test output
            truncated = test_output[-1500:] if len(test_output) > 1500 else test_output
            if len(test_output) > 1500:
                truncated = f"[...truncated {len(test_output) - 1500} chars...]\n" + truncated
            summary_parts.append(f"\n### Test output\n\n```\n{truncated}\n```")

            db.record_check_result(
                task_id, "gk-testing-octopoid", "fail",
                "\n".join(summary_parts)
            )
            self.log(f"gk-testing-octopoid FAILED for task {task_id}: test failures")

    def _check_divergence(self, review_sub: Path, agent_commits: list[str]) -> str:
        """Check if origin/sqlite-model has diverged since the agent forked.

        Compares the agent's base (parent of first commit) against current
        origin/sqlite-model. Returns a summary of what changed if diverged.

        Args:
            review_sub: Path to review worktree's orchestrator submodule
            agent_commits: List of agent commit hashes (oldest first)

        Returns:
            Human-readable summary of divergence, or empty string if no divergence
        """
        if not agent_commits:
            return ""

        first_commit = agent_commits[0]
        try:
            # Find the agent's base: parent of their first commit
            result = subprocess.run(
                ["git", "rev-parse", f"{first_commit}^"],
                cwd=review_sub,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return ""

            agent_base = result.stdout.strip()

            # Compare with current origin/sqlite-model
            result = subprocess.run(
                ["git", "rev-parse", "origin/sqlite-model"],
                cwd=review_sub,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return ""

            current_head = result.stdout.strip()

            if agent_base == current_head:
                return ""  # No divergence

            # Get the log of commits between agent base and current HEAD
            result = subprocess.run(
                ["git", "log", "--oneline", f"{agent_base}..origin/sqlite-model"],
                cwd=review_sub,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return ""

            upstream_commits = result.stdout.strip()
            commit_count = len(upstream_commits.strip().split("\n"))

            return (
                f"{commit_count} commit(s) landed on `sqlite-model` since the agent forked:\n\n"
                f"```\n{upstream_commits}\n```"
            )

        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return ""

    def _rebase_commits(self, review_sub: Path, commits: list[str]) -> tuple[bool, str]:
        """Rebase agent commits onto current origin/sqlite-model.

        Creates a temporary branch from the agent's first commit's parent,
        applies all agent commits, then rebases onto origin/sqlite-model.

        Args:
            review_sub: Path to review worktree's orchestrator submodule
            commits: List of commit hashes (oldest first)

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Strategy: checkout origin/sqlite-model, then cherry-pick agent commits
            # This achieves the same result as rebasing the agent's work onto the
            # latest base. We already reset to origin/sqlite-model in setup, so
            # we just need to cherry-pick in order.
            #
            # The difference from plain cherry-pick (pytest-submodule) is that
            # we provide better error context and divergence info.

            for commit in commits:
                result = subprocess.run(
                    ["git", "cherry-pick", "--no-commit", commit],
                    cwd=review_sub,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != 0:
                    # Get conflict details
                    conflict_output = result.stderr[:500] if result.stderr else ""

                    # Check for conflicted files
                    status_result = subprocess.run(
                        ["git", "diff", "--name-only", "--diff-filter=U"],
                        cwd=review_sub,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    conflicted_files = status_result.stdout.strip() if status_result.returncode == 0 else ""

                    # Get the commit message for context
                    msg_result = subprocess.run(
                        ["git", "log", "--format=%s", "-1", commit],
                        cwd=review_sub,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    commit_msg = msg_result.stdout.strip() if msg_result.returncode == 0 else commit[:8]

                    # Abort the cherry-pick
                    subprocess.run(
                        ["git", "cherry-pick", "--abort"],
                        cwd=review_sub,
                        capture_output=True,
                    )
                    # Reset to clean state
                    subprocess.run(
                        ["git", "reset", "--hard", "origin/sqlite-model"],
                        cwd=review_sub,
                        capture_output=True,
                    )

                    error_parts = [f"Conflict applying commit `{commit[:8]}` ({commit_msg})"]
                    if conflicted_files:
                        error_parts.append(f"\nConflicted files:\n```\n{conflicted_files}\n```")
                    if conflict_output:
                        error_parts.append(f"\nGit output:\n```\n{conflict_output}\n```")

                    return False, "\n".join(error_parts)

                # Commit the cherry-picked changes
                result = subprocess.run(
                    ["git", "commit", "--allow-empty", "-C", commit],
                    cwd=review_sub,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    # If commit failed, try to continue anyway (might be empty)
                    subprocess.run(
                        ["git", "reset"],
                        cwd=review_sub,
                        capture_output=True,
                    )

            return True, ""

        except subprocess.TimeoutExpired:
            subprocess.run(
                ["git", "cherry-pick", "--abort"],
                cwd=review_sub,
                capture_output=True,
            )
            subprocess.run(
                ["git", "reset", "--hard", "origin/sqlite-model"],
                cwd=review_sub,
                capture_output=True,
            )
            return False, "Rebase timed out"
        except subprocess.SubprocessError as e:
            return False, str(e)

    def _find_agent_worktree(self, claimed_by: str | None, task_id: str) -> Path | None:
        """Find the agent worktree for a task.

        Args:
            claimed_by: Agent name that worked on the task
            task_id: Task identifier (used to search worktrees if claimed_by missing)

        Returns:
            Path to agent worktree or None
        """
        agents_dir = get_orchestrator_dir() / "agents"

        # Try claimed_by first
        if claimed_by:
            worktree = agents_dir / claimed_by / "worktree"
            if worktree.exists():
                return worktree

        # Search worktrees for task-related branches
        for agent_dir in agents_dir.iterdir():
            if not agent_dir.is_dir() or agent_dir.name == "review-worktree":
                continue
            worktree = agent_dir / "worktree"
            if not worktree.exists():
                continue
            # Check if this worktree has the task
            state_path = agent_dir / "state.json"
            if state_path.exists():
                import json
                try:
                    state = json.loads(state_path.read_text())
                    if state.get("current_task") == task_id:
                        return worktree
                except (json.JSONDecodeError, IOError):
                    pass

        return None

    def _get_submodule_commits(self, submodule_path: Path) -> list[str]:
        """Get commit hashes in the submodule that are ahead of origin/sqlite-model.

        Args:
            submodule_path: Path to orchestrator submodule in agent worktree

        Returns:
            List of commit hashes (oldest first)
        """
        try:
            # Fetch to ensure we have origin/sqlite-model
            subprocess.run(
                ["git", "fetch", "origin", "sqlite-model"],
                cwd=submodule_path,
                capture_output=True,
                timeout=60,
            )

            result = subprocess.run(
                ["git", "log", "--format=%H", "origin/sqlite-model..HEAD"],
                cwd=submodule_path,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                self.debug_log(f"git log failed: {result.stderr}")
                return []

            commits = [c.strip() for c in result.stdout.strip().split("\n") if c.strip()]
            # Reverse so oldest is first (for cherry-pick order)
            commits.reverse()
            return commits

        except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            self.debug_log(f"Error getting submodule commits: {e}")
            return []

    def _setup_clean_submodule(self, review_sub: Path, agent_sub: Path) -> tuple[bool, str]:
        """Set up a clean submodule state in the review worktree.

        Fetches from the agent's submodule as a remote, then resets to
        origin/sqlite-model for a clean base.

        Args:
            review_sub: Path to review worktree's orchestrator submodule
            agent_sub: Path to agent's orchestrator submodule (used as fetch source)

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Fetch origin to get latest sqlite-model
            subprocess.run(
                ["git", "fetch", "origin", "sqlite-model"],
                cwd=review_sub,
                capture_output=True,
                timeout=60,
                check=True,
            )

            # Reset to origin/sqlite-model
            subprocess.run(
                ["git", "reset", "--hard", "origin/sqlite-model"],
                cwd=review_sub,
                capture_output=True,
                timeout=30,
                check=True,
            )

            # Clean untracked files
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=review_sub,
                capture_output=True,
                timeout=30,
            )

            # Add agent's submodule as a temporary remote for fetching commits
            subprocess.run(
                ["git", "remote", "remove", "agent-under-test"],
                cwd=review_sub,
                capture_output=True,
            )
            subprocess.run(
                ["git", "remote", "add", "agent-under-test", str(agent_sub)],
                cwd=review_sub,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "fetch", "agent-under-test"],
                cwd=review_sub,
                capture_output=True,
                timeout=60,
                check=True,
            )

            return True, ""

        except subprocess.CalledProcessError as e:
            return False, f"{e.cmd}: {e.stderr if hasattr(e, 'stderr') else str(e)}"
        except subprocess.TimeoutExpired:
            return False, "Setup timed out"

    def _cherry_pick_commits(self, review_sub: Path, commits: list[str]) -> tuple[bool, str]:
        """Cherry-pick commits into the review submodule.

        Args:
            review_sub: Path to review worktree's orchestrator submodule
            commits: List of commit hashes to cherry-pick (in order)

        Returns:
            Tuple of (success, error_message)
        """
        for commit in commits:
            try:
                result = subprocess.run(
                    ["git", "cherry-pick", commit],
                    cwd=review_sub,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != 0:
                    return False, f"Conflict on {commit[:8]}: {result.stderr[:300]}"
            except subprocess.TimeoutExpired:
                return False, f"Cherry-pick of {commit[:8]} timed out"
            except subprocess.SubprocessError as e:
                return False, str(e)

        return True, ""

    def _run_pytest(self, submodule_path: Path) -> tuple[bool, str]:
        """Run pytest in the submodule.

        Uses the orchestrator venv's python to run pytest.

        Args:
            submodule_path: Path to orchestrator submodule

        Returns:
            Tuple of (passed, output_text)
        """
        # Try multiple venv locations
        venv_candidates = [
            submodule_path / "venv" / "bin" / "python",
            self.parent_project / ".orchestrator" / "venv" / "bin" / "python",
        ]

        python_path = None
        for candidate in venv_candidates:
            if candidate.exists():
                python_path = candidate
                break

        if not python_path:
            return False, "Could not find orchestrator venv python"

        try:
            result = subprocess.run(
                [str(python_path), "-m", "pytest", "tests/", "-v", "--tb=short"],
                cwd=submodule_path,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout for tests
            )

            output = result.stdout + "\n" + result.stderr
            return result.returncode == 0, output

        except subprocess.TimeoutExpired:
            return False, "pytest timed out after 5 minutes"
        except subprocess.SubprocessError as e:
            return False, f"Failed to run pytest: {e}"

    def _process_check_results(self) -> None:
        """Process completed check results: reject failed tasks back to agents."""
        from .. import db

        provisional_tasks = db.list_tasks(queue="provisional")

        for task in provisional_tasks:
            task_id = task["id"]
            checks = task.get("checks", [])
            check_results = task.get("check_results", {})

            if not checks:
                continue

            # Check if all checks have been run
            all_run = all(
                c in check_results and check_results[c].get("status") in ("pass", "fail")
                for c in checks
            )

            if not all_run:
                continue

            # Check if any failed
            failed = [
                c for c in checks
                if check_results.get(c, {}).get("status") == "fail"
            ]

            if failed:
                # Aggregate failure feedback
                feedback_parts = []
                for check_name in failed:
                    result = check_results[check_name]
                    feedback_parts.append(
                        f"### {check_name}\n\n"
                        f"**FAILED** — {result.get('summary', 'No details')}\n"
                    )
                feedback = "\n".join(feedback_parts)

                task_path = task.get("path")
                if task_path:
                    self.log(f"Rejecting task {task_id}: failed checks {failed}")
                    review_reject_task(
                        str(task_path),
                        feedback,
                        rejected_by="check_runner",
                        max_rejections=3,
                    )
                else:
                    # No file path, update DB directly
                    db.review_reject_completion(
                        task_id,
                        reason=f"Failed checks: {', '.join(failed)}",
                        reviewer="check_runner",
                    )
            # If all passed, leave in provisional — reports.py will show in "in_review"
            # Human approves from there using the approval script


def main():
    main_entry(CheckRunnerRole)


if __name__ == "__main__":
    main()
