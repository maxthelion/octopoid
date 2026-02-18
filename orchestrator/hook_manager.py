"""Server-aware hook lifecycle management.

Bridges config-based hook resolution with server-enforced hook tracking.
Used by the scheduler for orchestrator-side hooks (merge_pr, etc.) and
for checking whether agent hooks are satisfied before state transitions.

Hook data model (stored on task via server):
    [
        {"name": "run_tests",  "point": "before_submit", "type": "agent",       "status": "pending"},
        {"name": "create_pr",  "point": "before_submit", "type": "agent",       "status": "pending"},
        {"name": "merge_pr",   "point": "before_merge",  "type": "orchestrator", "status": "pending"}
    ]

Hook types:
    - agent: Completed by agent scripts before finishing. Evidence recorded via API.
    - orchestrator: Run by HookManager on the scheduler side during state transitions.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .repo_manager import RepoManager

logger = logging.getLogger(__name__)

# Hooks that run on the orchestrator/scheduler side, not in the agent
ORCHESTRATOR_HOOKS = {"merge_pr"}

# All valid hook names (must match hooks.BUILTIN_HOOKS keys)
KNOWN_HOOKS = {"rebase_on_main", "create_pr", "run_tests", "merge_pr"}

# Default hooks when nothing is configured
DEFAULT_HOOKS = {
    "before_submit": ["create_pr"],
    "before_merge": ["merge_pr"],
}


@dataclass
class HookEvidence:
    """Evidence from a completed hook."""
    status: str  # "passed" or "failed"
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class HookManager:
    """Manages hook lifecycle for server-enforced hook tracking.

    Resolves hooks from config, converts to server data model,
    runs orchestrator-side hooks, and records evidence via SDK.

    Args:
        sdk: OctopoidSDK instance for API calls
        repo_manager_factory: Callable that takes (worktree, base_branch) and
            returns a RepoManager. Allows injection for testing.
    """

    def __init__(
        self,
        sdk: Any,
        repo_manager_factory: Callable[..., RepoManager] | None = None,
    ):
        self.sdk = sdk
        self.repo_manager_factory = repo_manager_factory or (
            lambda worktree, base_branch="main": RepoManager(worktree, base_branch)
        )

    def resolve_hooks_for_task(self, task_type: str | None = None) -> list[dict]:
        """Resolve hooks from config for a new task.

        Uses the same resolution order as hooks.resolve_hooks():
        1. Project-level hooks (hooks:)
        2. Default hooks

        Returns:
            List of hook dicts in server data model format, ready to
            be stored on the task via the API.
        """
        from .config import get_hooks_config

        hooks_config: dict[str, list[str]] | None = None

        # 1. Try project-level hooks
        hooks_config = get_hooks_config()

        # 2. If still empty, use defaults
        if not hooks_config:
            hooks_config = DEFAULT_HOOKS.copy()

        # Convert to server data model
        result = []
        for point, hook_names in hooks_config.items():
            for name in hook_names:
                if name not in KNOWN_HOOKS:
                    logger.warning("Unknown hook '%s', skipping", name)
                    continue
                hook_type = "orchestrator" if name in ORCHESTRATOR_HOOKS else "agent"
                result.append({
                    "name": name,
                    "point": point,
                    "type": hook_type,
                    "status": "pending",
                })

        return result

    def get_pending_hooks(
        self,
        task: dict,
        point: str | None = None,
        hook_type: str | None = None,
    ) -> list[dict]:
        """Get hooks that still need to be completed.

        Args:
            task: Task dict from API (must have 'hooks' field)
            point: Filter by hook point (e.g. "before_submit")
            hook_type: Filter by hook type ("agent" or "orchestrator")

        Returns:
            List of pending hook dicts.
        """
        hooks = self._get_hooks(task)
        pending = []
        for hook in hooks:
            if hook.get("status") != "pending":
                continue
            if point and hook.get("point") != point:
                continue
            if hook_type and hook.get("type") != hook_type:
                continue
            pending.append(hook)
        return pending

    def can_transition(self, task: dict, target_point: str) -> tuple[bool, list[str]]:
        """Check if all hooks for a transition point are satisfied.

        Args:
            task: Task dict from API
            target_point: The hook point to check (e.g. "before_submit", "before_merge")

        Returns:
            Tuple of (can_proceed, list of pending hook names).
        """
        pending = self.get_pending_hooks(task, point=target_point)
        pending_names = [h["name"] for h in pending]
        return len(pending) == 0, pending_names

    def run_orchestrator_hook(
        self,
        task: dict,
        hook: dict,
        worktree: Path | None = None,
    ) -> HookEvidence:
        """Execute an orchestrator-side hook.

        Currently supports:
        - merge_pr: Merges the task's PR via RepoManager

        Args:
            task: Task dict from API
            hook: Hook dict to execute
            worktree: Path to worktree (needed for git operations)

        Returns:
            HookEvidence with status and details.
        """
        name = hook["name"]

        if name == "merge_pr":
            return self._run_merge_pr(task, worktree)

        return HookEvidence(
            status="failed",
            message=f"Unknown orchestrator hook: {name}",
        )

    def _run_merge_pr(
        self,
        task: dict,
        worktree: Path | None = None,
    ) -> HookEvidence:
        """Merge the task's PR."""
        pr_number = task.get("pr_number")
        if not pr_number:
            return HookEvidence(
                status="passed",
                message="No PR to merge (skipped)",
            )

        from .config import get_base_branch
        base_branch = task.get("branch") or get_base_branch()
        # Use parent project as worktree if none provided
        effective_worktree = worktree or Path(".")
        repo = self.repo_manager_factory(effective_worktree, base_branch)

        merge_method = task.get("merge_method", "merge")
        success = repo.merge_pr(pr_number, method=merge_method)

        if success:
            return HookEvidence(
                status="passed",
                message=f"Merged PR #{pr_number}",
                data={"pr_number": pr_number, "pr_url": task.get("pr_url", "")},
            )
        else:
            return HookEvidence(
                status="failed",
                message=f"Failed to merge PR #{pr_number}",
                data={"pr_number": pr_number},
            )

    def record_evidence(
        self,
        task_id: str,
        hook_name: str,
        evidence: HookEvidence,
    ) -> dict | None:
        """Record hook completion evidence with the server.

        Calls the hook evidence endpoint on the server to update
        the hook's status. Once Phase 2 (server-side hooks) is
        implemented, this will use POST /api/v1/tasks/:id/hooks/:hookName/complete.

        For now, falls back to updating the task's hooks field via PATCH.

        Args:
            task_id: Task ID
            hook_name: Name of the hook that was completed
            evidence: HookEvidence with status and details

        Returns:
            Updated task dict, or None on failure.
        """
        try:
            # Get current task to read its hooks
            task = self.sdk.tasks.get(task_id)
            if not task:
                logger.error("Task %s not found", task_id)
                return None

            hooks = self._get_hooks(task)

            # Update the matching hook's status
            updated = False
            for hook in hooks:
                if hook["name"] == hook_name:
                    hook["status"] = evidence.status
                    if evidence.data:
                        hook["evidence"] = evidence.data
                    updated = True
                    break

            if not updated:
                logger.warning(
                    "Hook '%s' not found on task %s", hook_name, task_id
                )
                return task

            # Write back via PATCH
            import json
            return self.sdk.tasks.update(task_id, hooks=json.dumps(hooks))

        except Exception:
            logger.exception("Failed to record evidence for %s/%s", task_id, hook_name)
            return None

    @staticmethod
    def _get_hooks(task: dict) -> list[dict]:
        """Extract hooks list from task dict, handling JSON string or list."""
        hooks = task.get("hooks")
        if not hooks:
            return []
        if isinstance(hooks, str):
            import json
            try:
                hooks = json.loads(hooks)
            except json.JSONDecodeError:
                return []
        if isinstance(hooks, list):
            return hooks
        return []
