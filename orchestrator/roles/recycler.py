"""Recycler role - detects burned-out tasks and recycles them to re-breakdown.

The recycler polls the provisional queue for tasks that burned out
(0 commits after many turns) and sends them back through the breakdown
queue with enriched project context. It also reconciles stale blockers
so tasks don't get permanently stuck. This is a lightweight role that
doesn't need a worktree or invoke Claude.
"""

from ..config import is_db_enabled
from ..db import reconcile_stale_blockers
from ..queue_utils import (
    accept_completion,
    is_burned_out,
    list_tasks,
    recycle_to_breakdown,
)
from .base import BaseRole, main_entry


class RecyclerRole(BaseRole):
    """Recycler that detects burned-out tasks and sends them to re-breakdown."""

    def run(self) -> int:
        """Check provisional queue for burned-out tasks.

        Returns:
            Exit code (0 for success)
        """
        if not is_db_enabled():
            self.log("Recycler requires database mode to be enabled")
            return 0

        provisional_tasks = list_tasks("provisional")
        self.log(f"Found {len(provisional_tasks)} provisional tasks")

        recycled = 0
        accepted = 0

        for task in provisional_tasks:
            task_id = task["id"]
            task_path = task["path"]
            commits_count = task.get("commits_count", 0) or 0
            turns_used = task.get("turns_used", 0) or 0

            role = task.get("role")
            self.debug_log(f"Checking {task_id}: commits={commits_count}, turns={turns_used}, role={role}")

            if is_burned_out(commits_count=commits_count, turns_used=turns_used):
                self.log(f"Recycling {task_id}: burned out (0 commits, {turns_used} turns)")
                try:
                    result = recycle_to_breakdown(task_path)
                    if result and result.get("action") == "recycled":
                        self.log(f"Recycled {task_id} -> breakdown task {result['breakdown_task_id']}")
                        recycled += 1
                    elif result is None:
                        # Depth cap reached - accept for human review
                        self.log(f"Depth cap for {task_id}, accepting for human review")
                        accept_completion(task_path, accepted_by=self.agent_name)
                        accepted += 1
                except Exception as e:
                    self.log(f"Failed to recycle {task_id}: {e}")

        if recycled or accepted:
            self.log(f"Done: {recycled} recycled, {accepted} accepted (depth cap)")

        # Reconcile stale blockers: tasks blocked by done tasks get unblocked
        unblocked = reconcile_stale_blockers()
        for item in unblocked:
            self.log(
                f"Cleared stale blockers on {item['task_id']}: "
                f"{', '.join(item['stale_blockers'])}"
            )

        return 0


def main():
    main_entry(RecyclerRole)


if __name__ == "__main__":
    main()
