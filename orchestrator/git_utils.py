"""Git operations for worktrees, branches, and pull requests."""

import json
import subprocess
from datetime import datetime
from pathlib import Path

from .config import find_parent_project, get_agents_runtime_dir, get_tasks_dir


def run_git(args: list[str], cwd: Path | str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command.

    Args:
        args: Git command arguments (without 'git')
        cwd: Working directory for the command
        check: Raise exception on non-zero exit

    Returns:
        CompletedProcess instance
    """
    cmd = ["git"] + args
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
        timeout=120,
    )


def get_worktree_path(agent_name: str) -> Path:
    """Get the worktree path for an agent.

    Args:
        agent_name: Name of the agent

    Returns:
        Path to the agent's worktree directory
    """
    return get_agents_runtime_dir() / agent_name / "worktree"


def ensure_worktree(agent_name: str, base_branch: str = "main") -> Path:
    """Ensure a git worktree exists for an agent.

    Creates or updates the worktree for the agent.

    Args:
        agent_name: Name of the agent
        base_branch: Branch to base the worktree on

    Returns:
        Path to the worktree
    """
    import shutil

    parent_repo = find_parent_project()
    worktree_path = get_worktree_path(agent_name)

    # Check if worktree already exists and is valid
    if worktree_path.exists() and (worktree_path / ".git").exists():
        # Update existing worktree to latest origin/main
        try:
            run_git(["fetch", "origin"], cwd=worktree_path)
            # Reset to origin/main so the worktree isn't based on stale local main
            run_git(
                ["checkout", "--detach", f"origin/{base_branch}"],
                cwd=worktree_path,
                check=False,
            )
        except subprocess.CalledProcessError:
            pass  # Fetch may fail if offline, that's ok
        return worktree_path

    # Directory exists but is not a valid worktree - remove it
    if worktree_path.exists():
        # First try to remove it from git worktree list (if registered but broken)
        try:
            run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=parent_repo, check=False)
        except subprocess.CalledProcessError:
            pass
        # Then remove the directory itself
        if worktree_path.exists():
            shutil.rmtree(worktree_path)

    # Create parent directory
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    # Fetch latest from origin first
    try:
        run_git(["fetch", "origin"], cwd=parent_repo)
    except subprocess.CalledProcessError:
        pass  # May fail if offline

    # Create the worktree from origin/main (not local main) so it's
    # always up to date, even if the human hasn't run git pull.
    try:
        run_git(
            ["worktree", "add", "--detach", str(worktree_path), f"origin/{base_branch}"],
            cwd=parent_repo,
        )
    except subprocess.CalledProcessError as e:
        # Fallback to local branch if origin ref doesn't exist
        if "invalid reference" in e.stderr or "not a valid" in e.stderr:
            run_git(
                ["worktree", "add", "--detach", str(worktree_path), base_branch],
                cwd=parent_repo,
            )

    return worktree_path


def remove_worktree(agent_name: str) -> None:
    """Remove a git worktree for an agent.

    Args:
        agent_name: Name of the agent
    """
    parent_repo = find_parent_project()
    worktree_path = get_worktree_path(agent_name)

    if worktree_path.exists():
        run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=parent_repo, check=False)


# =============================================================================
# Task-scoped ephemeral worktrees
# =============================================================================

def get_task_worktree_path(task_id: str) -> Path:
    """Get the ephemeral worktree path for a task.

    Args:
        task_id: Task identifier (e.g., 'f7b4d710')

    Returns:
        Path to .orchestrator/tasks/<task-id>/worktree/
    """
    return get_tasks_dir() / task_id / "worktree"


def get_task_branch(task: dict) -> str:
    """Determine which branch a task should work on.

    Branch selection logic:
    - Project tasks: Use project.branch (all tasks in project share same branch)
    - Breakdown tasks: Use breakdown/<breakdown-id> branch
    - Standalone tasks: Use orch/<task-id> for orchestrator_impl, agent/<task-id> for app tasks

    Args:
        task: Task dictionary with keys: id, project_id, breakdown_id, role

    Returns:
        Branch name to checkout for this task
    """
    # Project tasks use the project's branch
    if task.get("project_id"):
        # Must fetch project from DB
        from . import db
        project = db.get_project(task["project_id"])
        if project and project.get("branch"):
            return project["branch"]

    # Breakdown tasks use a shared breakdown branch
    if task.get("breakdown_id"):
        return f"breakdown/{task['breakdown_id']}"

    # Standalone tasks use task-specific branches
    task_id = task["id"]
    role = task.get("role", "implement")

    if role == "orchestrator_impl":
        return f"orch/{task_id}"
    else:
        return f"agent/{task_id}"


def create_task_worktree(task: dict) -> Path:
    """Create an ephemeral worktree for a task.

    The worktree is created from origin, not local branches, ensuring
    freshness. If the target branch doesn't exist on origin, it's created
    from origin/main.

    Args:
        task: Task dictionary (must include 'id' and optionally project_id, breakdown_id, role)

    Returns:
        Path to the created worktree

    Raises:
        subprocess.CalledProcessError: If git commands fail
    """
    import shutil

    parent_repo = find_parent_project()
    task_id = task["id"]
    worktree_path = get_task_worktree_path(task_id)
    branch = get_task_branch(task)

    # Remove any existing worktree at this path (shouldn't happen, but be safe)
    if worktree_path.exists():
        if (worktree_path / ".git").exists():
            # Valid worktree exists — remove via git first
            try:
                run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=parent_repo, check=False)
            except subprocess.CalledProcessError:
                pass
        # Remove directory if it still exists
        if worktree_path.exists():
            shutil.rmtree(worktree_path)

    # Create parent directory
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    # Fetch latest from origin
    try:
        run_git(["fetch", "origin"], cwd=parent_repo, check=False)
    except subprocess.CalledProcessError:
        pass  # May fail if offline

    # Check if branch exists on origin
    result = run_git(
        ["ls-remote", "--heads", "origin", branch],
        cwd=parent_repo,
        check=False,
    )
    branch_exists_on_origin = bool(result.stdout.strip())

    if branch_exists_on_origin:
        # Pull existing branch from origin
        run_git(
            ["worktree", "add", "-b", branch, str(worktree_path), f"origin/{branch}"],
            cwd=parent_repo,
        )
    else:
        # Create new branch from origin/main
        run_git(
            ["worktree", "add", "-b", branch, str(worktree_path), "origin/main"],
            cwd=parent_repo,
        )

    return worktree_path


def cleanup_task_worktree(task_id: str, push_commits: bool = True) -> bool:
    """Clean up an ephemeral task worktree after task completion.

    Pushes any unpushed commits to origin, then deletes the worktree.

    Args:
        task_id: Task identifier
        push_commits: Whether to push unpushed commits before deleting (default True)

    Returns:
        True if cleanup succeeded, False otherwise
    """
    parent_repo = find_parent_project()
    worktree_path = get_task_worktree_path(task_id)

    if not worktree_path.exists():
        return True  # Already cleaned up

    try:
        # Push unpushed commits if requested
        if push_commits:
            # Check for unpushed commits
            result = run_git(
                ["rev-list", "@{u}..HEAD", "--count"],
                cwd=worktree_path,
                check=False,
            )
            if result.returncode == 0:
                unpushed_count = int(result.stdout.strip())
                if unpushed_count > 0:
                    # Push to origin
                    run_git(["push", "origin", "HEAD"], cwd=worktree_path, check=False)

        # Delete the worktree
        run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=parent_repo, check=False)

        return True

    except (subprocess.CalledProcessError, ValueError, OSError):
        # Log the error but don't fail — worktree deletion is best-effort
        return False


def create_feature_branch(
    worktree_path: Path,
    task_id: str,
    base_branch: str = "main",
) -> str:
    """Create a feature branch for a task.

    Args:
        worktree_path: Path to the agent's worktree
        task_id: Task identifier
        base_branch: Branch to base the feature branch on

    Returns:
        Name of the created branch
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    branch_name = f"agent/{task_id}-{timestamp}"

    # Fetch latest from origin and create branch from origin/main
    # (not local main, which may be behind if human hasn't run git pull)
    run_git(["fetch", "origin"], cwd=worktree_path, check=False)

    try:
        run_git(["checkout", "--detach", f"origin/{base_branch}"], cwd=worktree_path)
    except subprocess.CalledProcessError:
        # Fallback to local branch if origin ref doesn't exist
        run_git(["checkout", base_branch], cwd=worktree_path)

    # Create and checkout new branch from the detached origin HEAD
    run_git(["checkout", "-b", branch_name], cwd=worktree_path)

    return branch_name


def get_current_branch(worktree_path: Path) -> str:
    """Get the current branch name in a worktree.

    Args:
        worktree_path: Path to the worktree

    Returns:
        Current branch name
    """
    result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path)
    return result.stdout.strip()


def extract_task_id_from_branch(branch_name: str) -> str | None:
    """Extract task ID from an agent branch name.

    Agent branches follow the pattern: agent/{task_id}-{timestamp}
    Example: agent/9f5cda4b-20260203-214422 -> 9f5cda4b

    Args:
        branch_name: Name of the branch

    Returns:
        Task ID or None if not an agent branch
    """
    if not branch_name.startswith("agent/"):
        return None

    # Remove 'agent/' prefix
    suffix = branch_name[6:]

    # Task ID is everything before the timestamp (YYYYMMDD-HHMMSS)
    # Pattern: {task_id}-YYYYMMDD-HHMMSS
    import re
    match = re.match(r"^(.+)-\d{8}-\d{6}$", suffix)
    if match:
        return match.group(1)

    # Fallback: just take everything before the first dash-digit sequence
    parts = suffix.split("-")
    if parts:
        return parts[0]

    return None


def has_commits_ahead_of_base(worktree_path: Path, base_branch: str = "main") -> bool:
    """Check if current branch has commits ahead of base branch.

    Args:
        worktree_path: Path to the worktree
        base_branch: Branch to compare against

    Returns:
        True if there are commits on current branch not in base
    """
    try:
        result = run_git(
            ["rev-list", "--count", f"{base_branch}..HEAD"],
            cwd=worktree_path,
            check=False,
        )
        if result.returncode != 0:
            return False
        count = int(result.stdout.strip())
        return count > 0
    except (ValueError, subprocess.CalledProcessError):
        return False


def has_uncommitted_changes(worktree_path: Path) -> bool:
    """Check if worktree has uncommitted changes.

    Args:
        worktree_path: Path to the worktree

    Returns:
        True if there are uncommitted changes
    """
    result = run_git(["status", "--porcelain"], cwd=worktree_path)
    return bool(result.stdout.strip())


def commit_changes(worktree_path: Path, message: str) -> bool:
    """Commit all changes in the worktree.

    Args:
        worktree_path: Path to the worktree
        message: Commit message

    Returns:
        True if commit was made, False if nothing to commit
    """
    if not has_uncommitted_changes(worktree_path):
        return False

    run_git(["add", "-A"], cwd=worktree_path)
    run_git(["commit", "-m", message], cwd=worktree_path)
    return True


def push_branch(worktree_path: Path, branch_name: str) -> None:
    """Push a branch to origin.

    Args:
        worktree_path: Path to the worktree
        branch_name: Branch to push
    """
    run_git(["push", "-u", "origin", branch_name], cwd=worktree_path)


def create_pull_request(
    worktree_path: Path,
    branch_name: str,
    base_branch: str,
    title: str,
    body: str,
) -> str:
    """Create a pull request using gh CLI, or return existing PR URL.

    Args:
        worktree_path: Path to the worktree
        branch_name: Feature branch name
        base_branch: Target branch for the PR
        title: PR title
        body: PR body/description

    Returns:
        URL of the created or existing PR
    """
    # Push branch first
    push_branch(worktree_path, branch_name)

    # Check if PR already exists for this branch
    existing_pr = subprocess.run(
        ["gh", "pr", "view", branch_name, "--json", "url", "-q", ".url"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    if existing_pr.returncode == 0 and existing_pr.stdout.strip():
        # PR already exists, return its URL
        return existing_pr.stdout.strip()

    # Create new PR using gh
    result = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            base_branch,
            "--head",
            branch_name,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )

    # gh pr create outputs the PR URL
    return result.stdout.strip()


def count_open_prs(label: str | None = None) -> int:
    """Count open pull requests.

    Args:
        label: Optional label to filter by

    Returns:
        Number of open PRs
    """
    try:
        cmd = ["gh", "pr", "list", "--state", "open", "--json", "number"]
        if label:
            cmd.extend(["--label", label])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return 0

        prs = json.loads(result.stdout)
        return len(prs)
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError):
        return 0


def list_open_prs(author: str | None = None) -> list[dict]:
    """List open pull requests with details.

    Args:
        author: Optional author to filter by

    Returns:
        List of PR dictionaries with number, title, url, branch
    """
    try:
        cmd = [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,title,url,headRefName,author",
        ]
        if author:
            cmd.extend(["--author", author])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return []

        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError):
        return []


def cleanup_merged_branches(worktree_path: Path) -> list[str]:
    """Clean up local branches that have been merged.

    Args:
        worktree_path: Path to the worktree

    Returns:
        List of deleted branch names
    """
    deleted = []

    # Get list of merged branches
    result = run_git(["branch", "--merged", "main"], cwd=worktree_path, check=False)
    if result.returncode != 0:
        return deleted

    for line in result.stdout.strip().split("\n"):
        branch = line.strip().lstrip("* ")
        if branch and branch.startswith("agent/"):
            try:
                run_git(["branch", "-d", branch], cwd=worktree_path)
                deleted.append(branch)
            except subprocess.CalledProcessError:
                pass

    return deleted


def has_submodule_changes(worktree_path: Path, submodule_name: str = "orchestrator") -> bool:
    """Check if changes include a specific submodule.

    Submodule changes appear in git status as:
    - " M orchestrator" (modified submodule - new commits)
    - "?? orchestrator/" (untracked changes inside - shouldn't happen normally)

    Args:
        worktree_path: Path to the worktree
        submodule_name: Name of the submodule to check

    Returns:
        True if the submodule has uncommitted changes
    """
    result = run_git(["status", "--porcelain"], cwd=worktree_path, check=False)
    if result.returncode != 0:
        return False

    # Check if submodule appears in status output
    for line in result.stdout.strip().split("\n"):
        if line and submodule_name in line:
            return True
    return False


def has_uncommitted_submodule_changes(worktree_path: Path, submodule_name: str = "orchestrator") -> bool:
    """Check if the submodule itself has uncommitted changes.

    This checks inside the submodule for uncommitted work, not just
    whether the submodule pointer has changed in the parent.

    Args:
        worktree_path: Path to the worktree
        submodule_name: Name of the submodule

    Returns:
        True if there are uncommitted changes inside the submodule
    """
    submodule_path = worktree_path / submodule_name
    if not submodule_path.exists():
        return False

    return has_uncommitted_changes(submodule_path)


def get_submodule_unpushed_commits(worktree_path: Path, submodule_name: str = "orchestrator") -> list[str]:
    """Get list of unpushed commits in the submodule.

    Args:
        worktree_path: Path to the worktree
        submodule_name: Name of the submodule

    Returns:
        List of commit hashes that haven't been pushed to origin/main
    """
    submodule_path = worktree_path / submodule_name
    if not submodule_path.exists():
        return []

    # Fetch to make sure we have latest remote state
    run_git(["fetch", "origin"], cwd=submodule_path, check=False)

    # Get commits that are in HEAD but not in origin/main
    result = run_git(
        ["rev-list", "origin/main..HEAD"],
        cwd=submodule_path,
        check=False,
    )
    if result.returncode != 0:
        return []

    commits = result.stdout.strip().split("\n")
    return [c for c in commits if c]  # Filter empty strings


def push_submodule_to_main(
    worktree_path: Path,
    submodule_name: str = "orchestrator",
    commit_message: str | None = None,
) -> tuple[bool, str]:
    """Push submodule changes directly to the submodule's main branch.

    This commits any uncommitted changes in the submodule and pushes
    directly to origin/main. Use this for internal tooling submodules
    that don't need PR review.

    Args:
        worktree_path: Path to the worktree
        submodule_name: Name of the submodule
        commit_message: Optional commit message (auto-generated if not provided)

    Returns:
        Tuple of (success: bool, message: str)
    """
    submodule_path = worktree_path / submodule_name
    if not submodule_path.exists():
        return False, f"Submodule path does not exist: {submodule_path}"

    # Check if there are uncommitted changes to commit
    if has_uncommitted_changes(submodule_path):
        if not commit_message:
            commit_message = "Agent changes (auto-pushed)"

        run_git(["add", "-A"], cwd=submodule_path)
        run_git(["commit", "-m", commit_message], cwd=submodule_path)

    # Check if there are commits to push
    unpushed = get_submodule_unpushed_commits(worktree_path, submodule_name)
    if not unpushed:
        return True, "No commits to push"

    # Push to main
    try:
        result = run_git(
            ["push", "origin", "HEAD:main"],
            cwd=submodule_path,
        )
        return True, f"Pushed {len(unpushed)} commit(s) to {submodule_name} main"
    except subprocess.CalledProcessError as e:
        return False, f"Failed to push submodule: {e.stderr}"


def stage_submodule_pointer(worktree_path: Path, submodule_name: str = "orchestrator") -> bool:
    """Stage the submodule pointer change in the parent repo.

    After pushing submodule changes, call this to update the parent repo's
    reference to the new submodule commit.

    Args:
        worktree_path: Path to the worktree
        submodule_name: Name of the submodule

    Returns:
        True if the submodule was staged
    """
    try:
        run_git(["add", submodule_name], cwd=worktree_path)
        return True
    except subprocess.CalledProcessError:
        return False


def get_commit_count(
    worktree_path: Path,
    since_ref: str | None = None,
    branch: str | None = None,
) -> int:
    """Count commits on a branch.

    Args:
        worktree_path: Path to the worktree
        since_ref: Optional ref to count commits since (e.g., 'main', 'HEAD~5')
                   If None, counts commits since the branch diverged from main
        branch: Optional branch name to check instead of HEAD.
                Use this when the agent may have switched branches during work
                (e.g., orchestrator_impl agents working on orch/<task-id>).

    Returns:
        Number of commits
    """
    target = branch or "HEAD"
    try:
        if since_ref:
            # Count commits since the given ref
            result = run_git(
                ["rev-list", "--count", f"{since_ref}..{target}"],
                cwd=worktree_path,
                check=False,
            )
        else:
            # Count commits since diverging from main
            # First find the merge base
            merge_base_result = run_git(
                ["merge-base", target, "main"],
                cwd=worktree_path,
                check=False,
            )
            if merge_base_result.returncode != 0:
                # No common ancestor, count all commits
                result = run_git(
                    ["rev-list", "--count", target],
                    cwd=worktree_path,
                    check=False,
                )
            else:
                merge_base = merge_base_result.stdout.strip()
                result = run_git(
                    ["rev-list", "--count", f"{merge_base}..{target}"],
                    cwd=worktree_path,
                    check=False,
                )

        if result.returncode == 0:
            return int(result.stdout.strip())
        return 0
    except (subprocess.CalledProcessError, ValueError):
        return 0


def get_head_ref(worktree_path: Path) -> str:
    """Get the current HEAD commit SHA.

    Args:
        worktree_path: Path to the worktree

    Returns:
        SHA of HEAD, or empty string on error
    """
    try:
        result = run_git(["rev-parse", "HEAD"], cwd=worktree_path, check=False)
        if result.returncode == 0:
            return result.stdout.strip()
        return ""
    except subprocess.CalledProcessError:
        return ""


def get_submodule_status(worktree_path: Path, submodule_name: str = "orchestrator") -> dict[str, str | int | list[str]]:
    """Get git status of a submodule within a worktree.

    Useful for orchestrator_impl tasks where the real work happens
    inside the orchestrator/ submodule, not the main repo.

    Args:
        worktree_path: Path to the agent's worktree
        submodule_name: Name of the submodule directory (default: "orchestrator")

    Returns:
        Dictionary with keys:
        - exists (bool): Whether the submodule directory exists with a .git
        - branch (str): Current branch name, or "DETACHED" if detached HEAD
        - commits_ahead (int): Commits ahead of origin/<branch>
        - recent_commits (list[str]): Recent commit oneline summaries
        - diff_shortstat (str): Shortstat of unstaged changes
        - staged_shortstat (str): Shortstat of staged changes
        - untracked_count (int): Number of untracked files
        - warnings (list[str]): Any warnings (wrong branch, detached, etc.)
    """
    sub_path = worktree_path / submodule_name
    result: dict[str, str | int | list[str] | bool] = {
        "exists": False,
        "branch": "",
        "commits_ahead": 0,
        "recent_commits": [],
        "diff_shortstat": "",
        "staged_shortstat": "",
        "untracked_count": 0,
        "warnings": [],
    }

    # Check submodule exists
    if not sub_path.exists() or not (sub_path / ".git").exists():
        return result

    result["exists"] = True
    warnings: list[str] = []

    # Get branch
    try:
        branch_result = run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=sub_path, check=False
        )
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        branch = ""

    if branch == "HEAD":
        # Detached HEAD
        try:
            short_sha = run_git(
                ["rev-parse", "--short", "HEAD"], cwd=sub_path, check=False
            )
            branch = f"DETACHED@{short_sha.stdout.strip()}" if short_sha.returncode == 0 else "DETACHED"
        except (subprocess.SubprocessError, OSError):
            branch = "DETACHED"
        warnings.append("submodule HEAD is detached")
    elif branch and branch != "main":
        warnings.append(f"submodule on unexpected branch '{branch}' (expected main)")

    result["branch"] = branch

    # Count commits ahead of origin/<branch>
    remote_ref = f"origin/{branch}" if branch and not branch.startswith("DETACHED") else "origin/main"
    try:
        ahead_result = run_git(
            ["rev-list", "--count", f"{remote_ref}..HEAD"], cwd=sub_path, check=False
        )
        if ahead_result.returncode == 0:
            result["commits_ahead"] = int(ahead_result.stdout.strip())
    except (subprocess.SubprocessError, OSError, ValueError):
        pass

    # Recent commit log (up to 5)
    n = min(int(result["commits_ahead"]) if isinstance(result["commits_ahead"], int) else 0, 5)
    if n > 0:
        try:
            log_result = run_git(
                ["log", "--oneline", f"-{n}"], cwd=sub_path, check=False
            )
            if log_result.returncode == 0 and log_result.stdout.strip():
                result["recent_commits"] = log_result.stdout.strip().split("\n")
        except (subprocess.SubprocessError, OSError):
            pass

    # Unstaged changes
    try:
        diff_result = run_git(
            ["diff", "--shortstat"], cwd=sub_path, check=False
        )
        if diff_result.returncode == 0:
            result["diff_shortstat"] = diff_result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass

    # Staged changes
    try:
        staged_result = run_git(
            ["diff", "--cached", "--shortstat"], cwd=sub_path, check=False
        )
        if staged_result.returncode == 0:
            result["staged_shortstat"] = staged_result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass

    # Untracked files
    try:
        untracked_result = run_git(
            ["ls-files", "--others", "--exclude-standard"], cwd=sub_path, check=False
        )
        if untracked_result.returncode == 0 and untracked_result.stdout.strip():
            result["untracked_count"] = len(untracked_result.stdout.strip().split("\n"))
    except (subprocess.SubprocessError, OSError):
        pass

    result["warnings"] = warnings
    return result
