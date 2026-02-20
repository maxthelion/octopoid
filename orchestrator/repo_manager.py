"""High-level repository operations for worktrees.

Wraps git_utils into a testable class with structured return types.
Used by agent scripts and the HookManager for all git/PR operations.
"""

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class RebaseStatus(Enum):
    """Result of a rebase attempt."""
    SUCCESS = "success"
    CONFLICT = "conflict"
    UP_TO_DATE = "up_to_date"
    ERROR = "error"


@dataclass
class RepoStatus:
    """Current state of a worktree repository."""
    branch: str
    commits_ahead: int
    has_uncommitted: bool
    head_ref: str


@dataclass
class RebaseResult:
    """Result of a rebase operation."""
    status: RebaseStatus
    message: str = ""
    conflict_output: str = ""


@dataclass
class PrInfo:
    """Information about a pull request."""
    url: str
    number: int | None = None
    created: bool = False  # True if newly created, False if already existed


class RepoManager:
    """High-level git operations for a worktree.

    All operations run against the worktree path passed at construction.
    Delegates to git_utils where possible, adds structured return types.

    Args:
        worktree: Path to the git worktree
        base_branch: Branch to rebase onto / target for PRs (default: "main")
    """

    def __init__(self, worktree: Path, base_branch: str = "main"):
        self.worktree = worktree
        self.base_branch = base_branch

    def _run_git(
        self, args: list[str], check: bool = True, timeout: int = 120
    ) -> subprocess.CompletedProcess:
        """Run a git command in the worktree."""
        cmd = ["git"] + args
        return subprocess.run(
            cmd,
            cwd=self.worktree,
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
        )

    def _run_gh(
        self, args: list[str], check: bool = True, timeout: int = 60
    ) -> subprocess.CompletedProcess:
        """Run a gh CLI command in the worktree."""
        cmd = ["gh"] + args
        return subprocess.run(
            cmd,
            cwd=self.worktree,
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
        )

    # --- Status ---

    def get_status(self) -> RepoStatus:
        """Get the current state of the worktree.

        Returns:
            RepoStatus with branch name, commits ahead of base,
            whether there are uncommitted changes, and HEAD SHA.
        """
        # Branch name
        result = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], check=False)
        branch = result.stdout.strip() if result.returncode == 0 else ""

        # HEAD ref
        result = self._run_git(["rev-parse", "HEAD"], check=False)
        head_ref = result.stdout.strip() if result.returncode == 0 else ""

        # Commits ahead of base
        commits_ahead = 0
        result = self._run_git(
            ["rev-list", "--count", f"{self.base_branch}..HEAD"], check=False
        )
        if result.returncode == 0:
            try:
                commits_ahead = int(result.stdout.strip())
            except ValueError:
                pass

        # Uncommitted changes
        result = self._run_git(["status", "--porcelain"], check=False)
        has_uncommitted = bool(result.stdout.strip()) if result.returncode == 0 else False

        return RepoStatus(
            branch=branch,
            commits_ahead=commits_ahead,
            has_uncommitted=has_uncommitted,
            head_ref=head_ref,
        )

    # --- Branch & commit ---

    def ensure_on_branch(self, branch_name: str) -> str:
        """Ensure the worktree is on the specified branch.

        If already on the branch, does nothing.
        If on detached HEAD, creates the branch from HEAD.
        If on a different named branch, raises an error.

        Args:
            branch_name: The branch name to ensure

        Returns:
            The branch name

        Raises:
            RuntimeError: If on a different named branch than expected
        """
        status = self.get_status()

        if status.branch == branch_name:
            return branch_name

        if status.branch == "HEAD":
            # On detached HEAD — create branch from current position
            result = self._run_git(["checkout", "-b", branch_name], check=False)
            if result.returncode != 0:
                # Branch already exists locally — just check it out
                self._run_git(["checkout", branch_name])
            return branch_name

        raise RuntimeError(
            f"On branch '{status.branch}', expected '{branch_name}' or detached HEAD"
        )

    def push_branch(self, force: bool = False) -> str:
        """Push the current branch to origin.

        Args:
            force: Use --force-with-lease if True

        Returns:
            The branch name that was pushed.

        Raises:
            RuntimeError: If on detached HEAD (must create branch first)
            subprocess.CalledProcessError: If push fails.
        """
        status = self.get_status()
        if status.branch == "HEAD":
            raise RuntimeError(
                "Cannot push from detached HEAD. Create a branch first using ensure_on_branch()."
            )
        args = ["push", "-u", "origin", status.branch]
        if force:
            args.insert(1, "--force-with-lease")
        self._run_git(args)
        return status.branch

    def rebase_on_base(self) -> RebaseResult:
        """Fetch and rebase current branch onto the base branch.

        On conflict, aborts the rebase and returns CONFLICT status with
        the conflict output (so callers can decide how to handle it).

        Returns:
            RebaseResult with status, message, and conflict details.
        """
        # Fetch latest
        try:
            self._run_git(["fetch", "origin", self.base_branch], timeout=60)
        except subprocess.CalledProcessError as e:
            return RebaseResult(
                status=RebaseStatus.ERROR,
                message=f"Failed to fetch origin/{self.base_branch}: {e.stderr}",
            )
        except subprocess.TimeoutExpired:
            return RebaseResult(
                status=RebaseStatus.ERROR,
                message=f"Timeout fetching origin/{self.base_branch}",
            )

        # Check if rebase is needed
        result = self._run_git(
            ["rev-list", "--count", f"HEAD..origin/{self.base_branch}"], check=False
        )
        if result.returncode == 0:
            try:
                behind_count = int(result.stdout.strip())
                if behind_count == 0:
                    return RebaseResult(
                        status=RebaseStatus.UP_TO_DATE,
                        message="Already up to date with base branch",
                    )
            except ValueError:
                pass

        # Attempt rebase
        try:
            self._run_git(["rebase", f"origin/{self.base_branch}"], timeout=120)
            return RebaseResult(
                status=RebaseStatus.SUCCESS,
                message=f"Rebased on origin/{self.base_branch}",
            )
        except subprocess.CalledProcessError as e:
            # Abort the failed rebase
            self._run_git(["rebase", "--abort"], check=False, timeout=10)
            return RebaseResult(
                status=RebaseStatus.CONFLICT,
                message=f"Rebase conflict on origin/{self.base_branch}",
                conflict_output=e.stderr,
            )
        except subprocess.TimeoutExpired:
            self._run_git(["rebase", "--abort"], check=False, timeout=10)
            return RebaseResult(
                status=RebaseStatus.ERROR,
                message="Rebase timed out",
            )

    def reset_to_base(self) -> None:
        """Hard reset the worktree to origin/base_branch.

        Fetches latest first, then resets. Use with care.
        """
        self._run_git(["fetch", "origin", self.base_branch], check=False, timeout=60)
        self._run_git(["reset", "--hard", f"origin/{self.base_branch}"])

    # --- PR lifecycle ---

    def create_pr(self, title: str, body: str = "", task_branch: str | None = None) -> PrInfo:
        """Push branch and create a pull request. Idempotent.

        If a PR already exists for this branch, returns its info.

        Args:
            title: PR title
            body: PR body/description
            task_branch: Branch name to create if on detached HEAD (optional)

        Returns:
            PrInfo with URL, number, and whether it was newly created.

        Raises:
            subprocess.CalledProcessError: If push or PR creation fails.
            RuntimeError: If on detached HEAD and task_branch not provided
        """
        # If on detached HEAD and task_branch provided, create the branch first
        status = self.get_status()
        if status.branch == "HEAD":
            if not task_branch:
                raise RuntimeError(
                    "On detached HEAD but no task_branch provided. "
                    "Pass task_branch parameter to create_pr()."
                )
            self.ensure_on_branch(task_branch)

        # Push branch
        branch = self.push_branch()

        # Check if PR already exists
        result = self._run_gh(
            ["pr", "view", branch, "--json", "url,number", "-q", ".url + \" \" + (.number|tostring)"],
            check=False,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().rsplit(" ", 1)
            url = parts[0]
            number = int(parts[1]) if len(parts) > 1 else None
            return PrInfo(url=url, number=number, created=False)

        # Create new PR
        args = [
            "pr", "create",
            "--base", self.base_branch,
            "--head", branch,
            "--title", title,
        ]
        if body:
            args.extend(["--body", body])
        else:
            args.extend(["--body", ""])

        try:
            result = self._run_gh(args, timeout=60)
        except subprocess.CalledProcessError as e:
            # gh pr create fails if a PR already exists (e.g. the pr view check
            # above missed it due to a transient API error or rate limit).
            # Retry pr view before giving up.
            if "already exists" in (e.stderr or ""):
                retry = self._run_gh(
                    ["pr", "view", branch, "--json", "url,number",
                     "-q", '.url + " " + (.number|tostring)'],
                    check=False, timeout=30,
                )
                if retry.returncode == 0 and retry.stdout.strip():
                    parts = retry.stdout.strip().rsplit(" ", 1)
                    url = parts[0]
                    number = int(parts[1]) if len(parts) > 1 else None
                    return PrInfo(url=url, number=number, created=False)
            raise

        pr_url = result.stdout.strip()

        # Get the PR number from the URL (last path segment)
        pr_number = None
        if pr_url:
            try:
                pr_number = int(pr_url.rstrip("/").rsplit("/", 1)[-1])
            except (ValueError, IndexError):
                pass

        return PrInfo(url=pr_url, number=pr_number, created=True)

    def merge_pr(self, pr_number: int, method: str = "merge") -> bool:
        """Merge a pull request via gh CLI.

        Args:
            pr_number: PR number to merge
            method: Merge method ("merge", "squash", "rebase")

        Returns:
            True if merge succeeded, False otherwise.
        """
        result = self._run_gh(
            ["pr", "merge", str(pr_number), f"--{method}"],
            check=False,
            timeout=60,
        )
        return result.returncode == 0

    # --- Submodule ---

    def push_submodule(self, name: str, commit_message: str | None = None) -> bool:
        """Push submodule changes directly to the submodule's main branch.

        Commits any uncommitted changes in the submodule and pushes
        directly to origin/main.

        Args:
            name: Submodule directory name
            commit_message: Optional commit message

        Returns:
            True if push succeeded (or nothing to push).
        """
        sub_path = self.worktree / name
        if not sub_path.exists():
            return False

        # Check for uncommitted changes in submodule
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=sub_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if status.stdout.strip():
            msg = commit_message or "Agent changes (auto-pushed)"
            subprocess.run(["git", "add", "-A"], cwd=sub_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", msg], cwd=sub_path, check=True, capture_output=True
            )

        # Check for unpushed commits
        result = subprocess.run(
            ["git", "rev-list", "origin/main..HEAD"],
            cwd=sub_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return True  # Nothing to push

        # Push to main
        result = subprocess.run(
            ["git", "push", "origin", "HEAD:main"],
            cwd=sub_path,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def stage_submodule_pointer(self, name: str) -> bool:
        """Stage the submodule pointer change in the parent repo.

        After pushing submodule changes, call this to update the parent
        repo's reference to the new submodule commit.

        Args:
            name: Submodule directory name

        Returns:
            True if staging succeeded.
        """
        result = self._run_git(["add", name], check=False)
        return result.returncode == 0
