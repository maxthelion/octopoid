"""Step registry for flow-driven execution.

Each step is a function: (task: dict, result: dict, task_dir: Path) -> None
Steps are referenced by name in flow YAML `runs:` lists.
"""

from pathlib import Path
from typing import Callable

StepFn = Callable[[dict, dict, Path], None]

STEP_REGISTRY: dict[str, StepFn] = {}


def register_step(name: str) -> Callable:
    """Decorator to register a step function."""
    def decorator(fn: StepFn) -> StepFn:
        STEP_REGISTRY[name] = fn
        return fn
    return decorator


def execute_steps(step_names: list[str], task: dict, result: dict, task_dir: Path) -> None:
    """Execute a list of named steps in order."""
    for name in step_names:
        fn = STEP_REGISTRY.get(name)
        if fn is None:
            raise ValueError(f"Unknown step: {name}")
        fn(task, result, task_dir)


# =============================================================================
# Gatekeeper steps
# =============================================================================


@register_step("post_review_comment")
def post_review_comment(task: dict, result: dict, task_dir: Path) -> None:
    """Post the agent's review comment to the PR."""
    pr_number = task.get("pr_number")
    comment = result.get("comment", "")
    if pr_number and comment:
        from .pr_utils import add_pr_comment
        add_pr_comment(int(pr_number), comment)


@register_step("merge_pr")
def merge_pr(task: dict, result: dict, task_dir: Path) -> None:
    """Approve and merge the task's PR."""
    from . import queue_utils
    queue_utils.approve_and_merge(task["id"])


@register_step("reject_with_feedback")
def reject_with_feedback(task: dict, result: dict, task_dir: Path) -> None:
    """Reject task and return to incoming with feedback."""
    from . import queue_utils
    sdk = queue_utils.get_sdk()
    reason = result.get("comment", "Rejected by gatekeeper")
    sdk.tasks.reject(task["id"], reason=reason, rejected_by="gatekeeper")


# =============================================================================
# Implementer steps — to be implemented in TASK-2bf1ad9b
# =============================================================================

# @register_step("push_branch")
# @register_step("run_tests")
# @register_step("create_pr")
# @register_step("submit_to_server")
