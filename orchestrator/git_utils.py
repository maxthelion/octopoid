"""Git operations for worktrees, branches, and pull requests."""

import json
import subprocess
from datetime import datetime
from pathlib import Path

from .config import find_parent_project, get_agents_runtime_dir


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
        # Update existing worktree
        try:
            run_git(["fetch", "origin"], cwd=worktree_path)
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

    # Create the worktree in detached HEAD mode
    # This prevents blocking the branch from being checked out elsewhere
    try:
        run_git(
            ["worktree", "add", "--detach", str(worktree_path), base_branch],
            cwd=parent_repo,
        )
    except subprocess.CalledProcessError as e:
        # If branch doesn't exist locally, try with origin/branch
        if "invalid reference" in e.stderr or "not a valid" in e.stderr:
            run_git(
                ["worktree", "add", "--detach", str(worktree_path), f"origin/{base_branch}"],
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

    # Fetch latest
    run_git(["fetch", "origin"], cwd=worktree_path, check=False)

    # Checkout base branch and pull latest
    try:
        run_git(["checkout", base_branch], cwd=worktree_path)
        run_git(["pull", "origin", base_branch], cwd=worktree_path, check=False)
    except subprocess.CalledProcessError:
        # Try with origin prefix
        run_git(["checkout", "-B", base_branch, f"origin/{base_branch}"], cwd=worktree_path)

    # Create and checkout new branch
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
