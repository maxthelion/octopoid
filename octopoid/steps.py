"""Step registry for flow-driven execution.

Each step is a function: (task: dict, result: dict, task_dir: Path) -> None
Steps are referenced by name in flow YAML `runs:` lists.
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Callable

logger = logging.getLogger("octopoid.steps")

StepFn = Callable[[dict, dict, Path], None]

STEP_REGISTRY: dict[str, StepFn] = {}


class RetryableStepError(RuntimeError):
    """Raised by a step when the failure is transient and should be retried.

    For example, check_ci raises this when CI checks are still in progress.
    The caller should leave the task in its current state and retry on the
    next tick rather than treating this as a permanent failure.
    """


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


@register_step("check_ci")
def check_ci(task: dict, result: dict, task_dir: Path) -> None:
    """Verify that GitHub CI has passed before allowing merge_pr to proceed.

    Uses `gh pr checks` to inspect CI status. Outcomes:
    - No pr_number on task: no-op (graceful skip).
    - All checks passed: step succeeds, merge_pr may proceed.
    - Any check still pending/in-progress: raises RetryableStepError so the
      task stays in its current queue and is retried on the next scheduler tick.
    - Any check failed: raises RuntimeError with the name(s) of failed check(s).
    """
    pr_number = task.get("pr_number")
    if not pr_number:
        logger.debug("check_ci step: no pr_number on task, skipping")
        return

    try:
        proc = subprocess.run(
            ["gh", "pr", "checks", str(pr_number), "--json", "name,state,conclusion"],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise RetryableStepError("check_ci: gh pr checks timed out after 60s, will retry")
    except FileNotFoundError:
        logger.debug("check_ci step: gh CLI not found, skipping CI check")
        return

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        # If stdout is empty, the PR likely has no CI configured — treat as pass
        if not proc.stdout.strip():
            logger.debug(f"check_ci step: no CI checks found (gh exit {proc.returncode}), proceeding")
            return
        raise RetryableStepError(f"check_ci: failed to query CI checks: {stderr}")

    try:
        checks = json.loads(proc.stdout)
    except json.JSONDecodeError:
        logger.warning("check_ci step: could not parse gh output, skipping")
        return

    if not checks:
        logger.debug("check_ci step: no CI checks configured, proceeding")
        return

    _PENDING_STATES = {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED"}
    _FAILED_STATES = {"ERROR", "FAILURE"}
    _FAILED_CONCLUSIONS = {
        "FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED",
        "STARTUP_FAILURE", "ERROR",
    }

    failed_checks: list[str] = []
    pending_checks: list[str] = []

    for check in checks:
        name = check.get("name", "unknown")
        state = (check.get("state") or "").upper()
        conclusion = (check.get("conclusion") or "").upper()

        if state in _PENDING_STATES:
            pending_checks.append(name)
        elif state in _FAILED_STATES or conclusion in _FAILED_CONCLUSIONS:
            label = conclusion.lower() if conclusion else state.lower()
            failed_checks.append(f"{name} ({label})")

    if failed_checks:
        raise RuntimeError(
            f"check_ci: CI failed — {', '.join(failed_checks)}. "
            f"Fix the failures and push again."
        )

    if pending_checks:
        raise RetryableStepError(
            f"check_ci: CI still pending — {', '.join(pending_checks)}. "
            f"Waiting for checks to complete."
        )

    logger.info(f"check_ci step: all {len(checks)} CI check(s) passed")


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
            logger.warning(f"reject_with_feedback: failed to post PR comment: {e}")

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
        logger.warning(f"reject_with_feedback: failed to post thread message: {e}")

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
        logger.debug("run_tests step: no test runner detected, skipping")
        return

    # Build an environment with augmented PATH so npm/pnpm are findable even
    # when the scheduler runs under launchd with a minimal environment.
    env = os.environ.copy()
    env["PATH"] = _build_node_path()

    cmd = test_commands[0]
    logger.info(f"run_tests step: running {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd, cwd=worktree, capture_output=True, text=True, timeout=300, env=env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Tests timed out after 300s")
    except FileNotFoundError:
        logger.debug(f"run_tests step: test runner not found ({cmd[0]}), skipping")
        return

    if proc.returncode != 0:
        output = (proc.stdout + "\n" + proc.stderr)[-2000:]
        raise RuntimeError(f"Tests failed (exit code {proc.returncode}):\n{output}")

    logger.info("run_tests step: tests passed")


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
    logger.info(f"create_pr step: PR {pr.url} (new={pr.created})")

    # Store PR metadata on the task
    sdk = get_sdk()
    update_kwargs: dict = {}
    if pr.url:
        update_kwargs["pr_url"] = pr.url
    if pr.number is not None:
        update_kwargs["pr_number"] = pr.number
    if update_kwargs:
        sdk.tasks.update(task_id, **update_kwargs)


@register_step("rebase_on_base")
def rebase_on_base(task: dict, result: dict, task_dir: Path) -> None:
    """Rebase the worktree branch onto the repository's base branch (e.g. main).

    Ensures the PR is up-to-date before merge_pr runs, preventing rebase
    conflicts during the merge step. Aborts the rebase on failure so the
    worktree is left clean, then raises RuntimeError so the caller can
    reject the task back to incoming for retry.
    """
    from .config import get_base_branch

    base_branch = get_base_branch()
    worktree = task_dir / "worktree"

    fetch = subprocess.run(
        ["git", "fetch", "origin"],
        cwd=worktree, capture_output=True, text=True,
    )
    if fetch.returncode != 0:
        raise RuntimeError(f"rebase_on_base: git fetch failed:\n{fetch.stderr}")

    rebase = subprocess.run(
        ["git", "rebase", f"origin/{base_branch}"],
        cwd=worktree, capture_output=True, text=True,
    )
    if rebase.returncode != 0:
        subprocess.run(["git", "rebase", "--abort"], cwd=worktree, capture_output=True, text=True)
        raise RuntimeError(
            f"rebase_on_base: git rebase onto origin/{base_branch} failed:\n"
            f"{rebase.stdout}\n{rebase.stderr}"
        )

    logger.info(f"rebase_on_base: rebased onto origin/{base_branch}")


@register_step("rebase_on_project_branch")
def rebase_on_project_branch(task: dict, result: dict, task_dir: Path) -> None:
    """Rebase the worktree onto the project's shared branch.

    Fetches the project's branch via the SDK and rebases, so each child task
    sees the previous child's work.
    """
    from .sdk import get_sdk

    project_id = task.get("project_id")
    if not project_id:
        logger.debug("rebase_on_project_branch: no project_id on task, skipping")
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

    logger.info(f"rebase_on_project_branch: rebased onto origin/{project_branch}")


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
        logger.info(f"create_project_pr: PR already exists for {project_id}: {pr_url}")
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
            logger.info(f"create_project_pr: created PR {pr_url} for {project_id}")

    # Store PR metadata on the project (without changing status — the flow engine does that)
    if pr_url or pr_number is not None:
        sdk = get_sdk()
        update_kwargs: dict = {}
        if pr_url:
            update_kwargs["pr_url"] = pr_url
        if pr_number is not None:
            update_kwargs["pr_number"] = pr_number
        sdk.projects.update(project_id, **update_kwargs)


@register_step("update_changelog")
def update_changelog(task: dict, result: dict, task_dir: Path) -> None:
    """Read changes.md from task runtime dir and prepend to CHANGELOG.md on main.

    The agent writes ../changes.md (relative to the worktree) during its work.
    This step runs after merge_pr, reads that file, and inserts the content
    into CHANGELOG.md under ## [Unreleased], then commits and pushes to main.

    Skips silently if changes.md does not exist or is empty.
    """
    from .config import find_parent_project, get_base_branch

    changes_file = task_dir / "changes.md"
    if not changes_file.exists():
        logger.debug(f"update_changelog: no changes.md at {changes_file}, skipping")
        return

    changes_content = changes_file.read_text().strip()
    if not changes_content:
        logger.debug("update_changelog: changes.md is empty, skipping")
        return

    project_root = find_parent_project()
    changelog_path = project_root / "CHANGELOG.md"

    if not changelog_path.exists():
        logger.warning(f"update_changelog: CHANGELOG.md not found at {changelog_path}, skipping")
        return

    base_branch = get_base_branch()
    task_id = task["id"]
    task_title = task.get("title", task_id)

    try:
        # Pull latest before modifying so we don't clobber concurrent changes
        fetch = subprocess.run(
            ["git", "fetch", "origin"],
            cwd=project_root, capture_output=True, text=True,
        )
        if fetch.returncode != 0:
            raise RuntimeError(f"update_changelog: git fetch failed:\n{fetch.stderr}")

        pull = subprocess.run(
            ["git", "pull", "--rebase", "origin", base_branch],
            cwd=project_root, capture_output=True, text=True,
        )
        if pull.returncode != 0:
            raise RuntimeError(
                f"update_changelog: git pull --rebase failed:\n"
                f"{pull.stderr}"
            )

        # Re-read after pull in case CHANGELOG.md changed
        changelog = changelog_path.read_text()

        unreleased_marker = "## [Unreleased]"
        idx = changelog.find(unreleased_marker)
        if idx == -1:
            logger.warning("update_changelog: no '## [Unreleased]' section in CHANGELOG.md, skipping")
            return

        insert_at = idx + len(unreleased_marker)
        new_changelog = (
            changelog[:insert_at]
            + "\n\n"
            + changes_content
            + "\n"
            + changelog[insert_at:].lstrip("\n")
        )
        changelog_path.write_text(new_changelog)

        subprocess.run(
            ["git", "add", "CHANGELOG.md"],
            cwd=project_root, check=True, capture_output=True,
        )

        commit = subprocess.run(
            ["git", "commit", "-m", f"changelog: [{task_id}] {task_title}"],
            cwd=project_root, capture_output=True, text=True,
        )
        if commit.returncode != 0:
            if "nothing to commit" in commit.stdout or "nothing to commit" in commit.stderr:
                logger.debug("update_changelog: no changes to commit, skipping")
                return
            raise RuntimeError(f"update_changelog: git commit failed:\n{commit.stderr}")

        push = subprocess.run(
            ["git", "push", "origin", f"HEAD:{base_branch}"],
            cwd=project_root, capture_output=True, text=True,
        )
        if push.returncode != 0:
            raise RuntimeError(f"update_changelog: git push failed:\n{push.stderr}")

        logger.info(f"update_changelog: CHANGELOG.md updated for task {task_id}")

    except Exception as e:
        logger.warning(f"update_changelog failed for task {task_id} (non-fatal after merge): {e}")


@register_step("aggregate_child_changes")
def aggregate_child_changes(task: dict, result: dict, task_dir: Path) -> None:
    """Aggregate changes.md files from all child tasks into task_dir/changes.md.

    Used in project flows: reads each child task's changes.md from its runtime
    directory and concatenates them into task_dir/changes.md so that the
    subsequent update_changelog step can process them as normal.

    Skips silently if no child tasks exist or none have a changes.md file.
    """
    from .config import get_tasks_dir
    from .sdk import get_sdk

    project_id = task.get("id")
    if not project_id:
        logger.debug("aggregate_child_changes: no id on task, skipping")
        return

    sdk = get_sdk()
    try:
        child_tasks = sdk.projects.get_tasks(project_id)
    except Exception as e:
        logger.warning(f"aggregate_child_changes: failed to get child tasks for {project_id}: {e}")
        return

    if not child_tasks:
        logger.debug(f"aggregate_child_changes: no child tasks for project {project_id}, skipping")
        return

    tasks_dir = get_tasks_dir()
    aggregated_parts: list[str] = []

    for child_task in child_tasks:
        child_id = child_task.get("id")
        if not child_id:
            continue
        child_changes_file = tasks_dir / child_id / "changes.md"
        if not child_changes_file.exists():
            continue
        content = child_changes_file.read_text().strip()
        if content:
            aggregated_parts.append(content)

    if not aggregated_parts:
        logger.debug(f"aggregate_child_changes: no child changes.md files found for {project_id}, skipping")
        return

    output_file = task_dir / "changes.md"
    output_file.write_text("\n\n".join(aggregated_parts) + "\n")
    logger.info(
        f"aggregate_child_changes: aggregated {len(aggregated_parts)} child "
        f"changes.md file(s) for {project_id}"
    )


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

    logger.info(f"merge_project_pr: merged PR #{pr_number} for {project_id}")


