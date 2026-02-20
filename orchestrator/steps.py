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

    Also posts the feedback as a rejection message on the task thread so the
    next agent sees the full rejection history without any task file rewriting.
    """
    from . import queue_utils
    from .config import get_base_branch
    from .task_thread import post_message

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

    # Post rejection as a message on the task thread so the next agent sees it
    task_id = task["id"]
    try:
        post_message(task_id, role="rejection", content=reason, author="gatekeeper")
    except Exception as e:
        print(f"reject_with_feedback: failed to post thread message: {e}")

    sdk.tasks.reject(task_id, reason=reason, rejected_by="gatekeeper")


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


@register_step("create_project_pr")
def create_project_pr(project: dict, result: dict, project_dir: Path) -> None:
    """Create a PR for a project's shared branch. Stores PR metadata on the project.

    This step is used in project flows (not task flows). The 'project' dict is a
    project object (has 'id', 'title', 'branch', 'base_branch').
    project_dir is the parent project root directory used for gh CLI operations.
    """
    from .config import find_parent_project, get_base_branch
    from .sdk import get_sdk

    project_id = project["id"]
    project_title = project.get("title", project_id)
    project_branch = project.get("branch")

    if not project_branch:
        raise RuntimeError(f"create_project_pr: project {project_id} has no branch")

    base_branch = project.get("base_branch") or get_base_branch()
    cwd = project_dir if (project_dir and project_dir != Path(".")) else find_parent_project()

    pr_body = (
        f"## Project: {project_title}\n\n"
        f"All child tasks for project `{project_id}` are complete. "
        f"This PR merges the shared project branch into `{base_branch}`."
    )

    # Check if PR already exists for this branch
    pr_check = subprocess.run(
        [
            "gh", "pr", "view", project_branch,
            "--json", "url,number",
            "-q", '.url + " " + (.number|tostring)',
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )

    pr_url: str | None = None
    pr_number: int | None = None

    if pr_check.returncode == 0 and pr_check.stdout.strip():
        parts = pr_check.stdout.strip().rsplit(" ", 1)
        pr_url = parts[0]
        try:
            pr_number = int(parts[1]) if len(parts) > 1 else None
        except ValueError:
            pass
        print(f"create_project_pr: PR already exists for {project_id}: {pr_url}")
    else:
        pr_create = subprocess.run(
            [
                "gh", "pr", "create",
                "--base", base_branch,
                "--head", project_branch,
                "--title", f"[{project_id}] {project_title}",
                "--body", pr_body,
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if pr_create.returncode != 0:
            if "already exists" in (pr_create.stderr or ""):
                retry = subprocess.run(
                    [
                        "gh", "pr", "view", project_branch,
                        "--json", "url,number",
                        "-q", '.url + " " + (.number|tostring)',
                    ],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if retry.returncode == 0 and retry.stdout.strip():
                    parts = retry.stdout.strip().rsplit(" ", 1)
                    pr_url = parts[0]
                    try:
                        pr_number = int(parts[1]) if len(parts) > 1 else None
                    except ValueError:
                        pass
                else:
                    raise RuntimeError(
                        f"create_project_pr: PR creation failed for {project_id}: "
                        f"{pr_create.stderr.strip()}"
                    )
            else:
                raise RuntimeError(
                    f"create_project_pr: PR creation failed for {project_id}: "
                    f"{pr_create.stderr.strip()}"
                )
        else:
            pr_url = pr_create.stdout.strip()
            if pr_url:
                try:
                    pr_number = int(pr_url.rstrip("/").rsplit("/", 1)[-1])
                except (ValueError, IndexError):
                    pass
            print(f"create_project_pr: created PR {pr_url} for {project_id}")

    # Store PR metadata on the project (without changing status — the flow engine does that)
    if pr_url or pr_number is not None:
        sdk = get_sdk()
        update_kwargs: dict = {}
        if pr_url:
            update_kwargs["pr_url"] = pr_url
        if pr_number is not None:
            update_kwargs["pr_number"] = pr_number
        sdk.projects.update(project_id, **update_kwargs)


@register_step("merge_project_pr")
def merge_project_pr(project: dict, result: dict, project_dir: Path) -> None:
    """Merge the project's PR via gh CLI.

    Used in project flows for the 'provisional -> done' transition.
    Requires the project to have a 'pr_number' set (by create_project_pr).
    """
    from .config import find_parent_project

    project_id = project["id"]
    pr_number = project.get("pr_number")

    if not pr_number:
        raise RuntimeError(
            f"merge_project_pr: project {project_id} has no pr_number — "
            f"create_project_pr must run first"
        )

    cwd = project_dir if (project_dir and project_dir != Path(".")) else find_parent_project()

    merge_result = subprocess.run(
        ["gh", "pr", "merge", str(pr_number), "--merge"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if merge_result.returncode != 0:
        raise RuntimeError(
            f"merge_project_pr: failed to merge PR #{pr_number} for {project_id}: "
            f"{merge_result.stderr.strip()}"
        )

    print(f"merge_project_pr: merged PR #{pr_number} for {project_id}")


@register_step("submit_to_server")
def submit_to_server(task: dict, result: dict, task_dir: Path) -> None:
    """DEPRECATED: The flow engine now owns task transitions.

    This step used to submit a task to provisional via the server API. The
    engine now calls sdk.tasks.submit() automatically after steps complete,
    based on the flow YAML's to_state. This step is a no-op kept only for
    backwards compatibility with any existing flow YAML that still lists it.

    Remove from flow YAML `runs:` lists — it does nothing.
    """
    import warnings
    warnings.warn(
        "submit_to_server step is deprecated — the flow engine performs transitions "
        "automatically after steps complete. Remove it from your flow YAML runs list.",
        DeprecationWarning,
        stacklevel=2,
    )
    print(f"submit_to_server step: DEPRECATED no-op for task {task['id']} (engine owns transitions now)")
