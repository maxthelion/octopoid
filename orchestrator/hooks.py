"""Legacy execution pipeline for hooks — will be removed with implementer.py (Phase D2).

Provides declarative, configurable hook points that run during task processing.
Hooks are resolved per task type, with fallback to project defaults.

Hook points:
- BEFORE_SUBMIT: Runs agent-side before submitting completed work
- BEFORE_MERGE: Runs scheduler-side before merging/accepting a task

Built-in hooks:
- rebase_on_main: Fetch and rebase on base branch
- create_pr: Push branch and create a pull request
- run_tests: Detect and run test suite
- merge_pr: Merge a pull request via gh CLI
"""

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class HookPoint(Enum):
    """Points in the task lifecycle where hooks can run."""
    BEFORE_SUBMIT = "before_submit"
    BEFORE_MERGE = "before_merge"


class HookStatus(Enum):
    """Result status of a hook execution."""
    SUCCESS = "success"
    FAILURE = "failure"
    SKIP = "skip"


@dataclass
class HookResult:
    """Result of executing a single hook."""
    status: HookStatus
    message: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    remediation_prompt: str | None = None


@dataclass
class HookContext:
    """Context passed to hook functions during execution."""
    task_id: str
    task_title: str
    task_path: str
    task_type: str | None
    branch_name: str
    base_branch: str
    worktree: Path
    agent_name: str
    commits_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


# Type alias for hook functions
HookFn = Callable[[HookContext], HookResult]


# ---------------------------------------------------------------------------
# Built-in hooks
# ---------------------------------------------------------------------------


def hook_rebase_on_main(ctx: HookContext) -> HookResult:
    """Fetch base branch and rebase current work on top of it.

    On conflict, returns FAILURE with a remediation_prompt for Claude to
    resolve the conflicts and retry.
    """
    worktree = str(ctx.worktree)

    # Fetch latest from origin
    try:
        subprocess.run(
            ["git", "fetch", "origin", ctx.base_branch],
            cwd=worktree,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as e:
        return HookResult(
            status=HookStatus.FAILURE,
            message=f"Failed to fetch origin/{ctx.base_branch}: {e.stderr}",
        )
    except subprocess.TimeoutExpired:
        return HookResult(
            status=HookStatus.FAILURE,
            message=f"Timeout fetching origin/{ctx.base_branch}",
        )

    # Check if rebase is needed
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"HEAD..origin/{ctx.base_branch}"],
            cwd=worktree,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        behind_count = int(result.stdout.strip())
        if behind_count == 0:
            return HookResult(
                status=HookStatus.SKIP,
                message="Already up to date with base branch",
            )
    except (subprocess.CalledProcessError, ValueError):
        pass  # Proceed with rebase attempt anyway

    # Attempt rebase
    try:
        subprocess.run(
            ["git", "rebase", f"origin/{ctx.base_branch}"],
            cwd=worktree,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return HookResult(
            status=HookStatus.SUCCESS,
            message=f"Rebased on origin/{ctx.base_branch}",
        )
    except subprocess.CalledProcessError as e:
        # Rebase failed — likely conflicts. Abort and return remediation prompt.
        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=worktree,
            capture_output=True,
            timeout=10,
        )

        return HookResult(
            status=HookStatus.FAILURE,
            message=f"Rebase conflict on origin/{ctx.base_branch}",
            remediation_prompt=(
                f"The rebase of branch {ctx.branch_name} onto origin/{ctx.base_branch} "
                f"failed due to conflicts.\n\n"
                f"Conflict output:\n{e.stderr}\n\n"
                f"Please resolve the conflicts:\n"
                f"1. Run: git rebase origin/{ctx.base_branch}\n"
                f"2. For each conflicting file, edit to resolve the conflict markers\n"
                f"3. Stage resolved files: git add <file>\n"
                f"4. Continue: git rebase --continue\n"
                f"5. Repeat until rebase is complete\n\n"
                f"Work in: {ctx.worktree}"
            ),
        )
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=worktree,
            capture_output=True,
            timeout=10,
        )
        return HookResult(
            status=HookStatus.FAILURE,
            message="Rebase timed out",
        )


def hook_create_pr(ctx: HookContext) -> HookResult:
    """Push branch and create a pull request via git_utils."""
    from .git_utils import create_pull_request

    stdout_summary = ctx.extra.get("stdout", "")
    if len(stdout_summary) > 2000:
        stdout_summary = stdout_summary[-2000:]

    pr_body = f"""## Summary

Automated implementation for task [{ctx.task_id}].

## Task

{ctx.task_title}

## Changes

{stdout_summary}

---
Generated by orchestrator agent: {ctx.agent_name}
"""

    try:
        pr_url = create_pull_request(
            ctx.worktree,
            ctx.branch_name,
            ctx.base_branch,
            f"[{ctx.task_id}] {ctx.task_title}",
            pr_body,
        )
        return HookResult(
            status=HookStatus.SUCCESS,
            message=f"Created PR: {pr_url}",
            context={"pr_url": pr_url},
        )
    except Exception as e:
        return HookResult(
            status=HookStatus.FAILURE,
            message=f"Failed to create PR: {e}",
        )


def hook_run_tests(ctx: HookContext) -> HookResult:
    """Detect and run the project test suite.

    Looks for common test runners (pytest, npm test, make test) and
    runs the first one found. On failure, returns a remediation prompt
    with the test output so Claude can fix the issues.
    """
    worktree = str(ctx.worktree)

    # Detect test runner
    test_commands = []
    if (ctx.worktree / "pytest.ini").exists() or (ctx.worktree / "pyproject.toml").exists():
        test_commands.append(["python", "-m", "pytest", "--tb=short", "-q"])
    if (ctx.worktree / "package.json").exists():
        test_commands.append(["npm", "test"])
    if (ctx.worktree / "Makefile").exists():
        test_commands.append(["make", "test"])

    if not test_commands:
        return HookResult(
            status=HookStatus.SKIP,
            message="No test runner detected",
        )

    # Run the first detected test command
    cmd = test_commands[0]
    try:
        result = subprocess.run(
            cmd,
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            return HookResult(
                status=HookStatus.SUCCESS,
                message="Tests passed",
            )
        else:
            output = result.stdout + "\n" + result.stderr
            if len(output) > 3000:
                output = output[-3000:]
            return HookResult(
                status=HookStatus.FAILURE,
                message=f"Tests failed (exit code {result.returncode})",
                remediation_prompt=(
                    f"Tests failed when running `{' '.join(cmd)}`.\n\n"
                    f"Test output:\n{output}\n\n"
                    f"Please fix the failing tests and commit your changes.\n"
                    f"Work in: {ctx.worktree}"
                ),
            )
    except subprocess.TimeoutExpired:
        return HookResult(
            status=HookStatus.FAILURE,
            message="Tests timed out after 300s",
        )
    except FileNotFoundError:
        return HookResult(
            status=HookStatus.SKIP,
            message=f"Test runner not found: {cmd[0]}",
        )


def hook_merge_pr(ctx: HookContext) -> HookResult:
    """Merge a pull request via gh CLI.

    Reads ``pr_number`` and optional ``merge_method`` from ``ctx.extra``.
    Returns SKIP if no ``pr_number`` is present (allows tasks without PRs
    to pass through).
    """
    pr_number = ctx.extra.get("pr_number")
    if not pr_number:
        return HookResult(
            status=HookStatus.SKIP,
            message="No pr_number in context, skipping merge",
        )

    merge_method = ctx.extra.get("merge_method", "merge")
    merge_cmd = [
        "gh", "pr", "merge", str(pr_number),
        f"--{merge_method}",
    ]

    try:
        result = subprocess.run(
            merge_cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return HookResult(
                status=HookStatus.SUCCESS,
                message=f"Merged PR #{pr_number}",
                context={
                    "pr_number": pr_number,
                    "pr_url": ctx.extra.get("pr_url", ""),
                },
            )
        else:
            return HookResult(
                status=HookStatus.FAILURE,
                message=f"Failed to merge PR #{pr_number}: {result.stderr}",
            )
    except subprocess.TimeoutExpired:
        return HookResult(
            status=HookStatus.FAILURE,
            message=f"Timeout merging PR #{pr_number}",
        )
    except subprocess.SubprocessError as e:
        return HookResult(
            status=HookStatus.FAILURE,
            message=f"Error merging PR #{pr_number}: {e}",
        )


# ---------------------------------------------------------------------------
# Registry — canonical DEFAULT_HOOKS lives in hook_manager.py
# ---------------------------------------------------------------------------

BUILTIN_HOOKS: dict[str, HookFn] = {
    "rebase_on_main": hook_rebase_on_main,
    "create_pr": hook_create_pr,
    "run_tests": hook_run_tests,
    "merge_pr": hook_merge_pr,
}

from .hook_manager import DEFAULT_HOOKS  # noqa: E402


# ---------------------------------------------------------------------------
# Resolution and execution
# ---------------------------------------------------------------------------


def resolve_hooks(hook_point: HookPoint, task_type: str | None = None) -> list[HookFn]:
    """Resolve which hooks to run for a given hook point and task type.

    Resolution order:
    1. Task has type → use task_types.<type>.hooks.<point>
    2. No type or type has no hooks for this point → use top-level hooks.<point>
    3. No top-level hooks → use DEFAULT_HOOKS

    Returns:
        Ordered list of hook functions to execute
    """
    from .config import get_hooks_for_type, get_hooks_config

    point_name = hook_point.value

    # 1. Try task-type-specific hooks
    if task_type:
        type_hooks = get_hooks_for_type(task_type)
        if type_hooks and point_name in type_hooks:
            return _names_to_functions(type_hooks[point_name])

    # 2. Try project-level hooks
    project_hooks = get_hooks_config()
    if point_name in project_hooks:
        return _names_to_functions(project_hooks[point_name])

    # 3. Fall back to defaults
    if point_name in DEFAULT_HOOKS:
        return _names_to_functions(DEFAULT_HOOKS[point_name])

    return []


def _names_to_functions(hook_names: list[str]) -> list[HookFn]:
    """Convert a list of hook names to their corresponding functions."""
    functions = []
    for name in hook_names:
        if name in BUILTIN_HOOKS:
            functions.append(BUILTIN_HOOKS[name])
        else:
            # Unknown hook name — skip with warning
            print(f"Warning: Unknown hook '{name}', skipping")
    return functions


def run_hooks(
    hook_point: HookPoint,
    ctx: HookContext,
) -> tuple[bool, list[HookResult]]:
    """Execute hooks for a given hook point. Fail-fast on first failure.

    Args:
        hook_point: Which lifecycle point to run hooks for
        ctx: Context about the current task

    Returns:
        Tuple of (all_ok, list of HookResults).
        all_ok is True if all hooks succeeded or were skipped.
    """
    hooks = resolve_hooks(hook_point, ctx.task_type)
    results: list[HookResult] = []

    if not hooks:
        return True, results

    for hook_fn in hooks:
        result = hook_fn(ctx)
        results.append(result)

        if result.status == HookStatus.FAILURE:
            return False, results

    return True, results
