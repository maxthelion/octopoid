"""Pre-check role - scheduler-level pre-check for provisional task completions.

The pre-check decides whether a submission is worth sending to gatekeeper
review. It runs BEFORE gatekeepers and answers: "should we bother reviewing
this?" — not "is this code good?"

Checks:
- Accepts tasks with commits (passes to gatekeeper review or human review)
- Rejects tasks without commits (moves back to incoming for retry)
- Recycles burned-out tasks (0 commits, high turns) to re-breakdown
- Escalates to planning if too many failed attempts

This role is lightweight - it doesn't need a worktree or invoke Claude.
"""

from pathlib import Path

from ..config import get_pre_check_config, is_db_enabled
from ..queue_utils import (
    accept_completion,
    escalate_to_planning,
    is_burned_out,
    list_tasks,
    recycle_to_breakdown,
    reject_completion,
)
from .base import BaseRole, main_entry


class PreCheckRole(BaseRole):
    """Scheduler pre-check that filters provisional completions before review."""

    def run(self) -> int:
        """Pre-check provisional tasks.

        Returns:
            Exit code (0 for success)
        """
        if not is_db_enabled():
            self.log("Pre-check requires database mode to be enabled")
            return 0

        pre_check_config = get_pre_check_config()
        require_commits = pre_check_config["require_commits"]
        max_attempts = pre_check_config["max_attempts_before_planning"]
        claim_timeout = pre_check_config["claim_timeout_minutes"]

        # Process provisional tasks
        provisional_tasks = list_tasks("provisional")
        self.log(f"Found {len(provisional_tasks)} provisional tasks")

        for task in provisional_tasks:
            task_id = task["id"]
            task_path = task["path"]
            commits_count = task.get("commits_count", 0)
            turns_used = task.get("turns_used", 0)
            attempt_count = task.get("attempt_count", 0)

            role = task.get("role")

            # Skip tasks with pending gatekeeper checks
            checks = task.get("checks", [])
            if checks:
                check_results = task.get("check_results", {})
                has_pending = any(
                    c not in check_results or check_results[c].get("status") not in ("pass", "fail")
                    for c in checks
                )
                if has_pending:
                    self.debug_log(f"Skipping {task_id}: has pending gatekeeper checks")
                    continue

            self.debug_log(f"Pre-checking {task_id}: commits={commits_count}, turns={turns_used}, attempts={attempt_count}, role={role}")

            # Check if task has commits
            if require_commits and commits_count == 0:
                # Immediate catch: burned out (0 commits, high turns) -> recycle
                if is_burned_out(commits_count=commits_count, turns_used=turns_used):
                    self._recycle_task(task_id, task_path, turns_used)
                # Cumulative catch: too many failed attempts -> recycle for project tasks, escalate otherwise
                elif attempt_count >= max_attempts:
                    self._recycle_or_escalate_task(task_id, task_path, attempt_count)
                else:
                    reject_completion(
                        task_path,
                        reason="no_commits",
                        accepted_by=self.agent_name,
                    )
                    self.log(f"Task {task_id} rejected (attempt {attempt_count + 1})")
            else:
                # Accept the task
                accept_completion(task_path, accepted_by=self.agent_name)
                self.log(f"Accepted {task_id} ({commits_count} commits)")

        # Reset stuck claimed tasks
        self._reset_stuck_claimed(claim_timeout)

        # Check for unblocked tasks
        self._check_unblocked_tasks()

        return 0

    def _recycle_task(self, task_id: str, task_path: Path, turns_used: int) -> None:
        """Recycle a burned-out task to re-breakdown.

        Args:
            task_id: Task identifier
            task_path: Path to the task file
            turns_used: Number of turns used
        """
        self.log(f"Recycling {task_id}: burned out (0 commits, {turns_used} turns)")

        try:
            result = recycle_to_breakdown(task_path)
            if result and result.get("action") == "recycled":
                self.log(f"Recycled {task_id} -> breakdown task {result['breakdown_task_id']}")
            elif result is None:
                # Depth cap reached - accept with warning for human review
                self.log(f"Depth cap reached for {task_id}, accepting for human review")
                accept_completion(task_path, accepted_by=self.agent_name)
        except Exception as e:
            self.log(f"Failed to recycle {task_id}: {e}")
            reject_completion(
                task_path,
                reason="recycle_failed",
                accepted_by=self.agent_name,
            )

    def _recycle_or_escalate_task(self, task_id: str, task_path: Path, attempt_count: int) -> None:
        """Recycle a task with too many failed attempts, or escalate if no project.

        For project tasks, recycling to breakdown is preferred over planning escalation.
        For standalone tasks, falls back to the existing planning escalation.

        Args:
            task_id: Task identifier
            task_path: Path to the task file
            attempt_count: Number of attempts made
        """
        # Try recycling first (works for project tasks)
        self.log(f"Task {task_id} failed {attempt_count} times, attempting recycle")

        try:
            result = recycle_to_breakdown(task_path, reason="cumulative_failures")
            if result and result.get("action") == "recycled":
                self.log(f"Recycled {task_id} -> breakdown task {result['breakdown_task_id']}")
                return
        except Exception:
            pass

        # Fall back to planning escalation
        self._escalate_task(task_id, task_path, attempt_count)

    def _escalate_task(self, task_id: str, task_path: Path, attempt_count: int) -> None:
        """Escalate a task to planning after too many failed attempts.

        Args:
            task_id: Task identifier
            task_path: Path to the task file
            attempt_count: Number of attempts made
        """
        from ..planning import create_planning_task

        self.log(f"Escalating {task_id} to planning (after {attempt_count} attempts)")

        try:
            plan_id = create_planning_task(task_id, task_path)
            escalate_to_planning(task_path, plan_id)
            self.log(f"Created planning task {plan_id} for {task_id}")
        except Exception as e:
            self.log(f"Failed to escalate {task_id}: {e}")
            # Fall back to rejection
            reject_completion(
                task_path,
                reason="escalation_failed",
                accepted_by=self.agent_name,
            )

    def _reset_stuck_claimed(self, timeout_minutes: int) -> None:
        """Reset tasks that have been claimed too long.

        Args:
            timeout_minutes: How long a task can be claimed before reset
        """
        from .. import db

        reset_ids = db.reset_stuck_claimed(timeout_minutes)
        if reset_ids:
            self.log(f"Reset {len(reset_ids)} stuck claimed tasks: {reset_ids}")

    def _check_unblocked_tasks(self) -> None:
        """Check if any tasks have been unblocked by completed dependencies."""
        from .. import db

        # Get tasks that might be blocked
        tasks = db.list_tasks(queue="incoming", include_blocked=True)
        unblocked_count = 0

        for task in tasks:
            if task.get("blocked_by"):
                if db.check_dependencies_resolved(task["id"]):
                    # Dependencies are resolved, clear the blocked_by field
                    db.update_task(task["id"], blocked_by=None)
                    unblocked_count += 1
                    self.debug_log(f"Unblocked task {task['id']}")

        if unblocked_count > 0:
            self.log(f"Unblocked {unblocked_count} tasks")


def main():
    main_entry(PreCheckRole)


if __name__ == "__main__":
    main()
