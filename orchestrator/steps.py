"""Step registry for flow-driven execution.

Each step is a function: (task: dict, result: dict, task_dir: Path) -> None
Steps are referenced by name in flow YAML `runs:` lists.
"""

import os
import subprocess
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
    """Approve and merge the task's PR. Raises RuntimeError on failure."""
    from . import queue_utils
    outcome = queue_utils.approve_and_merge(task["id"])
    if outcome and "error" in outcome:
        raise RuntimeError(f"merge_pr failed: {outcome['error']}")


@register_step("reject_with_feedback")
def reject_with_feedback(task: dict, result: dict, task_dir: Path) -> None:
    """Reject task and return to incoming with feedback.

    Posts the review comment to the PR (so it's visible to both humans and
    the implementer when they check the PR) and rejects the task via the SDK.
    Appends explicit rebase instructions to the rejection reason if not already
    present, so the implementer knows to rebase before retrying.
    """
    from . import queue_utils
    from .config import get_base_branch

    sdk = queue_utils.get_sdk()
    comment = result.get("comment", "Rejected by gatekeeper")

    # Post the review comment to the PR so it's visible to humans and implementers
    pr_number = task.get("pr_number")
    if pr_number and comment:
        from .pr_utils import add_pr_comment
        try:
            add_pr_comment(int(pr_number), comment)
        except Exception as e:
            print(f"reject_with_feedback: failed to post PR comment: {e}")

    # Append explicit rebase instructions if not already present in the comment
    base_branch = get_base_branch()
    rebase_instructions = (
        f"\n\n**Before Retrying:**\n"
        f"Rebase your branch onto the base branch before making changes:\n"
        f"```bash\n"
        f"git fetch origin\n"
        f"git rebase origin/{base_branch}\n"
        f"```\n"
        f"Then fix the issues above and push again."
    )
    if "git rebase" not in comment:
        reason = comment + rebase_instructions
    else:
        reason = comment

    sdk.tasks.reject(task["id"], reason=reason, rejected_by="gatekeeper")


# =============================================================================
# Implementer steps
# =============================================================================


@register_step("push_branch")
def push_branch(task: dict, result: dict, task_dir: Path) -> None:
    """Ensure worktree is on the task branch and push to remote."""
    from .git_utils import get_task_branch
    from .repo_manager import RepoManager

    worktree = task_dir / "worktree"
    branch = get_task_branch(task)
    repo = RepoManager(worktree)
    repo.ensure_on_branch(branch)
    repo.push_branch()


def _build_node_path() -> str:
    """Build a PATH string that includes common node/pnpm locations.

    The scheduler runs under launchd with a minimal PATH that often omits
    nvm-managed node versions and pnpm. This ensures subprocesses can find
    npm, pnpm, and node regardless of how the scheduler was launched.
    """
    extra_paths: list[str] = []

    # Include nvm's currently-active node version bin directory
    home = Path.home()
    nvm_dir = Path(os.environ.get("NVM_DIR", home / ".nvm"))
    nvm_versions = nvm_dir / "versions" / "node"
    if nvm_versions.is_dir():
        # Pick the highest-versioned node (sorted lexicographically — good enough for vX.Y.Z)
        versions = sorted(nvm_versions.iterdir(), reverse=True)
        if versions:
            extra_paths.append(str(versions[0] / "bin"))

    # corepack shims live alongside npm in the global node installation
    for node_prefix in ("/usr/local", str(home / ".local")):
        shims = Path(node_prefix) / "lib" / "node_modules" / "corepack" / "shims"
        if shims.is_dir():
            extra_paths.append(str(shims))

    # Existing PATH (may already have some useful entries)
    existing = os.environ.get("PATH", "")
    all_paths = extra_paths + ([existing] if existing else [])
    return ":".join(all_paths)


@register_step("run_tests")
def run_tests(task: dict, result: dict, task_dir: Path) -> None:
    """Run the project test suite. Raises RuntimeError on failure."""
    worktree = task_dir / "worktree"

    # Detect test runner
    test_commands: list[list[str]] = []
    if (worktree / "pytest.ini").exists() or (worktree / "pyproject.toml").exists():
        test_commands.append(["python", "-m", "pytest", "--tb=short", "-q"])
    if (worktree / "package.json").exists():
        test_commands.append(["npm", "test"])
    if (worktree / "Makefile").exists():
        test_commands.append(["make", "test"])

    if not test_commands:
        print("run_tests step: no test runner detected, skipping")
        return

    # Build an environment with augmented PATH so npm/pnpm are findable even
    # when the scheduler runs under launchd with a minimal environment.
    env = os.environ.copy()
    env["PATH"] = _build_node_path()

    cmd = test_commands[0]
    print(f"run_tests step: running {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd, cwd=worktree, capture_output=True, text=True, timeout=300, env=env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Tests timed out after 300s")
    except FileNotFoundError:
        print(f"run_tests step: test runner not found ({cmd[0]}), skipping")
        return

    if proc.returncode != 0:
        output = (proc.stdout + "\n" + proc.stderr)[-2000:]
        raise RuntimeError(f"Tests failed (exit code {proc.returncode}):\n{output}")

    print("run_tests step: tests passed")


@register_step("create_pr")
def create_pr(task: dict, result: dict, task_dir: Path) -> None:
    """Push the task branch and create a PR. Stores PR metadata on the task."""
    from .repo_manager import RepoManager
    from .sdk import get_sdk

    task_id = task["id"]
    task_title = task.get("title", task_id)
    worktree = task_dir / "worktree"

    # Build PR body from recent commits
    try:
        log = subprocess.run(
            ["git", "log", "origin/HEAD..HEAD", "--oneline"],
            cwd=worktree, capture_output=True, text=True, check=False,
        )
        commits_summary = log.stdout.strip() if log.returncode == 0 else ""
    except Exception:
        commits_summary = ""

    pr_body = (
        f"## Summary\n\n"
        f"Automated implementation for task [{task_id}].\n\n"
        f"## Changes\n\n```\n{commits_summary}\n```\n"
    )

    repo = RepoManager(worktree, base_branch=task.get("branch", "main"))
    pr = repo.create_pr(title=f"[{task_id}] {task_title}", body=pr_body)
    print(f"create_pr step: PR {pr.url} (new={pr.created})")

    # Store PR metadata on the task
    sdk = get_sdk()
    update_kwargs: dict = {}
    if pr.url:
        update_kwargs["pr_url"] = pr.url
    if pr.number is not None:
        update_kwargs["pr_number"] = pr.number
    if update_kwargs:
        sdk.tasks.update(task_id, **update_kwargs)


@register_step("rebase_on_project_branch")
def rebase_on_project_branch(task: dict, result: dict, task_dir: Path) -> None:
    """Rebase the worktree onto the project's shared branch.

    Fetches the project's branch via the SDK and rebases, so each child task
    sees the previous child's work.
    """
    from .sdk import get_sdk

    project_id = task.get("project_id")
    if not project_id:
        print("rebase_on_project_branch: no project_id on task, skipping")
        return

    sdk = get_sdk()
    project = sdk.projects.get(project_id)
    if not project:
        raise RuntimeError(f"rebase_on_project_branch: project {project_id} not found")

    project_branch = project.get("branch")
    if not project_branch:
        raise RuntimeError(f"rebase_on_project_branch: project {project_id} has no branch")

    worktree = task_dir / "worktree"

    fetch = subprocess.run(
        ["git", "fetch", "origin"],
        cwd=worktree, capture_output=True, text=True,
    )
    if fetch.returncode != 0:
        raise RuntimeError(f"rebase_on_project_branch: git fetch failed:\n{fetch.stderr}")

    rebase = subprocess.run(
        ["git", "rebase", f"origin/{project_branch}"],
        cwd=worktree, capture_output=True, text=True,
    )
    if rebase.returncode != 0:
        raise RuntimeError(
            f"rebase_on_project_branch: git rebase failed:\n{rebase.stdout}\n{rebase.stderr}"
        )

    print(f"rebase_on_project_branch: rebased onto origin/{project_branch}")


@register_step("submit_to_server")
def submit_to_server(task: dict, result: dict, task_dir: Path) -> None:
    """Submit the task to provisional via the server API."""
    from .sdk import get_sdk

    task_id = task["id"]
    worktree = task_dir / "worktree"

    # Count commits ahead of base
    commits = 0
    try:
        count = subprocess.run(
            ["git", "rev-list", "--count", "origin/HEAD..HEAD"],
            cwd=worktree, capture_output=True, text=True, check=False,
        )
        if count.returncode == 0:
            commits = int(count.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        pass

    sdk = get_sdk()
    sdk.tasks.submit(task_id, commits_count=commits, turns_used=0)
    print(f"submit_to_server step: task {task_id} submitted (commits={commits})")
