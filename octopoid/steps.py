"""Step registry for flow-driven execution.

Steps are either Step objects (with pre_check/execute/verify phases) or
legacy functions: (task: dict, result: dict, task_dir: Path) -> None.
Steps are referenced by name in flow YAML `runs:` lists.

The three-phase Step protocol prevents ghost completions and non-idempotent
retries:
  - pre_check(): detect already-done work and skip safely
  - execute(): perform the action
  - verify(): confirm the action took durable effect (raises StepVerificationError)
"""

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger("octopoid.steps")

StepFn = Callable[[dict, dict, Path], None]


# =============================================================================
# Error types
# =============================================================================


class RetryableStepError(RuntimeError):
    """Raised by a step when the failure is transient and should be retried.

    The caller should leave the task in its current state and retry on the
    next tick rather than treating this as a permanent failure.
    """


class StepVerificationError(RuntimeError):
    """Raised when execute() succeeds but verify() confirms the action did not take effect.

    Indicates a ghost completion: the step appeared to succeed but the durable
    outcome is missing. The step should be retried on the next tick.
    """


class PermanentStepError(RuntimeError):
    """Raised when a step fails in a way that will not succeed on retry.

    Signals that human intervention is needed. The caller should move the
    task to requires-intervention rather than retrying.
    """


# =============================================================================
# Step protocol
# =============================================================================


@dataclass
class StepContext:
    """Everything a step needs — replaces the (task, result, task_dir) tuple."""
    task: dict
    result: dict
    task_dir: Path


class Step:
    """Base class for flow steps with pre_check, execute, and verify phases.

    Subclasses must implement execute(). Override check_done() for idempotency
    and verify() for post-execution verification.

    Step instances are also callable as old-style functions for backwards
    compatibility:  step(task, result, task_dir)  →  step.execute(ctx)
    """
    name: str = ""

    def check_done(self, ctx: StepContext) -> bool:
        """Is this step's action already done?

        The core idempotency check. Called by both pre_check (to decide whether
        to skip execution) and verify (to confirm the action took effect).
        Override this in steps that can detect completion externally.
        Default: always False (step cannot self-report completion).
        """
        return False

    def pre_check(self, ctx: StepContext) -> bool:
        """Return True if the step is already done and should be skipped.

        Default implementation delegates to check_done().
        """
        return self.check_done(ctx)

    def execute(self, ctx: StepContext) -> None:
        """Perform the step's action. May raise on failure."""
        raise NotImplementedError(f"Step {self.name!r} must implement execute()")

    def verify(self, ctx: StepContext) -> None:
        """Confirm the action took durable effect after execute() runs.

        Default: no verification (backwards compatible for steps where
        external verification isn't meaningful, e.g. run_tests).
        Override to call check_done() and raise StepVerificationError if False.
        """
        pass

    def __call__(self, task: dict, result: dict, task_dir: Path) -> None:
        """Support old-style function call API for backwards compatibility.

        Allows Step instances to be called as:  step(task, result, task_dir)
        Used by tests that import step names directly from the module.
        """
        ctx = StepContext(task=task, result=result, task_dir=task_dir)
        self.execute(ctx)


# =============================================================================
# Step registry
# =============================================================================

STEP_REGISTRY: dict[str, "Step | StepFn"] = {}


def register_step(name: str) -> Callable:
    """Decorator to register a step (function or Step subclass).

    When used as a class decorator on a Step subclass, instantiates the class,
    sets its name, registers the instance in STEP_REGISTRY, and returns the
    instance (so the module-level name is the Step instance, not the class).

    When used as a function decorator, registers the function unchanged.
    """
    def decorator(fn_or_cls):
        if isinstance(fn_or_cls, type) and issubclass(fn_or_cls, Step):
            # Class decorator — instantiate and return the instance
            instance = fn_or_cls()
            instance.name = name
            STEP_REGISTRY[name] = instance
            return instance
        else:
            # Old-style step function
            STEP_REGISTRY[name] = fn_or_cls
            return fn_or_cls
    return decorator


def _write_step_progress(task_dir: Path, completed: list[str], failed: str | None) -> None:
    """Write step progress to task_dir/step_progress.json for intervention context."""
    try:
        progress_path = task_dir / "step_progress.json"
        progress_path.write_text(json.dumps({"completed": completed, "failed": failed}, indent=2))
    except OSError:
        pass


def execute_steps(step_names: list[str], task: dict, result: dict, task_dir: Path) -> None:
    """Execute a list of named steps in order.

    Supports both new-style Step objects (with pre_check/execute/verify) and
    old-style step functions for backwards compatibility during migration.

    For Step objects:
    - Calls pre_check first; if True, skips execute/verify (step already done)
    - After execute, calls verify to confirm the action took durable effect
    - Raises StepVerificationError if verify fails
    - Raises RetryableStepError for transient failures (caller keeps PID)

    Writes step_progress.json to task_dir after each step so that
    intervention_context can record which steps completed before a failure.
    """
    ctx = StepContext(task=task, result=result, task_dir=task_dir)
    completed: list[str] = []
    for name in step_names:
        entry = STEP_REGISTRY.get(name)
        if entry is None:
            _write_step_progress(task_dir, completed, failed=name)
            raise ValueError(f"Unknown step: {name}")

        if isinstance(entry, Step):
            try:
                if entry.pre_check(ctx):
                    logger.info(f"Step {name}: pre_check passed, skipping (already done)")
                    completed.append(name)
                    _write_step_progress(task_dir, completed, failed=None)
                    continue
                entry.execute(ctx)
                entry.verify(ctx)
                completed.append(name)
                _write_step_progress(task_dir, completed, failed=None)
            except (RetryableStepError, StepVerificationError, PermanentStepError):
                _write_step_progress(task_dir, completed, failed=name)
                raise
            except Exception:
                _write_step_progress(task_dir, completed, failed=name)
                raise
        else:
            # Old-style step function
            try:
                entry(task, result, task_dir)
                completed.append(name)
                _write_step_progress(task_dir, completed, failed=None)
            except Exception:
                _write_step_progress(task_dir, completed, failed=name)
                raise


# =============================================================================
# Gatekeeper steps
# =============================================================================


@register_step("post_review_comment")
class _PostReviewCommentStep(Step):
    """Post the agent's review comment to the PR. Best-effort, no verify."""

    def execute(self, ctx: StepContext) -> None:
        pr_number = ctx.task.get("pr_number")
        comment = ctx.result.get("comment", "")
        if pr_number and comment:
            from .pr_utils import add_pr_comment
            add_pr_comment(int(pr_number), comment)


# Re-export as the original function name for backwards compatibility
post_review_comment = STEP_REGISTRY["post_review_comment"]



@register_step("merge_pr")
class _MergePrStep(Step):
    """Approve and merge the task's PR.

    pre_check: PR is already MERGED? Skip (handles ghost completions where PR
               was merged but the SDK call to mark task done failed).
    verify: PR state is MERGED after merge attempt.
    """

    def check_done(self, ctx: StepContext) -> bool:
        """Check if the PR is already in MERGED state."""
        pr_number = ctx.task.get("pr_number")
        if not pr_number:
            return False
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "state", "-q", ".state"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return False
        return result.stdout.strip().upper() == "MERGED"

    def execute(self, ctx: StepContext) -> None:
        from . import queue_utils
        outcome = queue_utils.approve_and_merge(ctx.task["id"])
        if outcome and "error" in outcome:
            raise RuntimeError(f"merge_pr failed: {outcome['error']}")

    def verify(self, ctx: StepContext) -> None:
        if not self.check_done(ctx):
            pr_number = ctx.task.get("pr_number")
            raise StepVerificationError(
                f"merge_pr verify failed: PR #{pr_number} not in MERGED state after merge attempt"
            )


merge_pr = STEP_REGISTRY["merge_pr"]


@register_step("reject_with_feedback")
class _RejectWithFeedbackStep(Step):
    """Reject task and return to incoming with feedback.

    Posts the review comment to the PR (so it's visible to both humans and
    the implementer when they check the PR) and rejects the task via the SDK.
    Appends explicit rebase instructions to the rejection reason if not already
    present, so the implementer knows to rebase before retrying.

    Also posts the feedback as a rejection message on the task thread so the
    next agent sees the full rejection history without any task file rewriting.

    check_done: task is in incoming queue or has rejection_reason (already rejected).
    verify: confirms task was rejected on server after execute().
    """

    def check_done(self, ctx: StepContext) -> bool:
        """Check if the task has already been rejected (back in incoming queue)."""
        from .sdk import get_sdk
        sdk = get_sdk()
        task = sdk.tasks.get(ctx.task["id"])
        if not task:
            return False
        return task.get("queue") == "incoming" or bool(task.get("rejection_reason"))

    def execute(self, ctx: StepContext) -> None:
        from . import queue_utils
        from .config import get_base_branch
        from .task_thread import post_message

        task = ctx.task
        result = ctx.result
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

    def verify(self, ctx: StepContext) -> None:
        if not self.check_done(ctx):
            raise StepVerificationError(
                f"reject_with_feedback verify failed: task {ctx.task['id']} "
                f"is not in incoming queue after rejection"
            )


reject_with_feedback = STEP_REGISTRY["reject_with_feedback"]


# =============================================================================
# Implementer steps
# =============================================================================


@register_step("push_branch")
class _PushBranchStep(Step):
    """Ensure worktree is on the task branch and push to remote.

    pre_check: Branch already exists on remote? Skip (prevents failures
               when a previous attempt partially pushed the branch).
    verify: Branch exists on remote after push attempt.
    """

    def check_done(self, ctx: StepContext) -> bool:
        """Check if the task branch already exists on the remote."""
        from .git_utils import get_task_branch
        worktree = ctx.task_dir / "worktree"
        branch = get_task_branch(ctx.task)
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}"],
            cwd=worktree, capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0

    def execute(self, ctx: StepContext) -> None:
        from .git_utils import get_task_branch
        from .repo_manager import RepoManager
        worktree = ctx.task_dir / "worktree"
        branch = get_task_branch(ctx.task)
        repo = RepoManager(worktree)
        repo.ensure_on_branch(branch)
        repo.push_branch()

    def verify(self, ctx: StepContext) -> None:
        if not self.check_done(ctx):
            from .git_utils import get_task_branch
            branch = get_task_branch(ctx.task)
            raise StepVerificationError(
                f"push_branch verify failed: branch '{branch}' not found on remote after push"
            )


push_branch = STEP_REGISTRY["push_branch"]


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
class _RunTestsStep(Step):
    """Run the project test suite. Raises RuntimeError on failure.

    No pre_check or verify — tests must always run and exit code is the outcome.
    """

    def execute(self, ctx: StepContext) -> None:
        worktree = ctx.task_dir / "worktree"

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


run_tests = STEP_REGISTRY["run_tests"]


@register_step("create_pr")
class _CreatePrStep(Step):
    """Push the task branch and create a PR. Stores PR metadata on the task.

    pre_check: PR already exists for this branch? Skip (and store pr_number
               so subsequent steps can use it if a previous run created the PR
               but failed to record it).
    verify: PR exists on GitHub and pr_number is stored on the task.
    """

    def check_done(self, ctx: StepContext) -> bool:
        """Check if a PR already exists for this branch."""
        worktree = ctx.task_dir / "worktree"
        from .git_utils import get_task_branch
        branch = get_task_branch(ctx.task)
        result = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "number"],
            cwd=worktree, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        try:
            data = json.loads(result.stdout)
            return bool(data.get("number"))
        except json.JSONDecodeError:
            return False

    def pre_check(self, ctx: StepContext) -> bool:
        """If PR already exists, store its metadata and skip execute."""
        worktree = ctx.task_dir / "worktree"
        from .git_utils import get_task_branch
        branch = get_task_branch(ctx.task)
        result = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "number,url"],
            cwd=worktree, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        try:
            data = json.loads(result.stdout)
            pr_number = data.get("number")
            pr_url = data.get("url")
            if pr_number:
                # Store metadata so subsequent steps can use pr_number
                from .sdk import get_sdk
                sdk = get_sdk()
                update_kwargs: dict = {}
                if pr_url:
                    update_kwargs["pr_url"] = pr_url
                update_kwargs["pr_number"] = pr_number
                try:
                    sdk.tasks.update(ctx.task["id"], **update_kwargs)
                except Exception as e:
                    logger.warning(f"create_pr pre_check: failed to store PR metadata: {e}")
                logger.info(f"create_pr pre_check: PR #{pr_number} already exists, skipping")
                return True
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"create_pr pre_check: error checking PR: {e}")
        return False

    def execute(self, ctx: StepContext) -> None:
        from .repo_manager import RepoManager
        from .sdk import get_sdk

        task = ctx.task
        task_id = task["id"]
        task_title = task.get("title", task_id)
        worktree = ctx.task_dir / "worktree"

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

    def verify(self, ctx: StepContext) -> None:
        """Verify PR exists on GitHub and pr_number is stored on the task."""
        if not self.check_done(ctx):
            raise StepVerificationError(
                "create_pr verify failed: PR not found on GitHub after creation"
            )
        # Also verify pr_number was stored on the task
        from .sdk import get_sdk
        sdk = get_sdk()
        task = sdk.tasks.get(ctx.task["id"])
        if not task or not task.get("pr_number"):
            raise StepVerificationError(
                "create_pr verify failed: pr_number not stored on task after PR creation"
            )


create_pr = STEP_REGISTRY["create_pr"]


@register_step("rebase_on_base")
class _RebaseOnBaseStep(Step):
    """Rebase the worktree branch onto the repository's base branch (e.g. main).

    Ensures the PR is up-to-date before merge_pr runs, preventing rebase
    conflicts during the merge step. Aborts the rebase on failure so the
    worktree is left clean.

    pre_check: HEAD already a descendant of origin/base? Skip (fetches first).
    verify: HEAD is a descendant of origin/base after rebase.
    """

    def check_done(self, ctx: StepContext) -> bool:
        """Check if HEAD is already a descendant of origin/base_branch."""
        from .config import get_base_branch
        base_branch = get_base_branch()
        worktree = ctx.task_dir / "worktree"
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", f"origin/{base_branch}", "HEAD"],
            cwd=worktree, capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0

    def pre_check(self, ctx: StepContext) -> bool:
        """Fetch and check if HEAD is already a descendant of origin/base_branch."""
        from .config import get_base_branch
        base_branch = get_base_branch()
        worktree = ctx.task_dir / "worktree"
        # Fetch to get latest remote state before checking
        subprocess.run(
            ["git", "fetch", "origin", base_branch],
            cwd=worktree, capture_output=True, text=True, timeout=60,
        )
        return self.check_done(ctx)

    def execute(self, ctx: StepContext) -> None:
        from .config import get_base_branch
        base_branch = get_base_branch()
        worktree = ctx.task_dir / "worktree"

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

    def verify(self, ctx: StepContext) -> None:
        if not self.check_done(ctx):
            from .config import get_base_branch
            base_branch = get_base_branch()
            raise StepVerificationError(
                f"rebase_on_base verify failed: HEAD is not a descendant of "
                f"origin/{base_branch} after rebase"
            )


rebase_on_base = STEP_REGISTRY["rebase_on_base"]


@register_step("rebase_on_project_branch")
class _RebaseOnProjectBranchStep(Step):
    """Rebase the worktree onto the project's shared branch.

    Fetches the project's branch via the SDK and rebases, so each child task
    sees the previous child's work.

    check_done: HEAD is already a descendant of origin/project_branch (merge-base --is-ancestor).
    pre_check: fetch project branch then check_done.
    verify: HEAD is a descendant of origin/project_branch after rebase.
    """

    def _get_project_branch(self, ctx: StepContext) -> str | None:
        """Fetch project branch from SDK. Returns None if no project_id. Raises on not found."""
        project_id = ctx.task.get("project_id")
        if not project_id:
            return None
        from .sdk import get_sdk
        sdk = get_sdk()
        project = sdk.projects.get(project_id)
        if not project:
            raise RuntimeError(f"rebase_on_project_branch: project {project_id} not found")
        project_branch = project.get("branch")
        if not project_branch:
            raise RuntimeError(f"rebase_on_project_branch: project {project_id} has no branch")
        return project_branch

    def check_done(self, ctx: StepContext) -> bool:
        """Check if HEAD is already a descendant of origin/project_branch."""
        project_branch = self._get_project_branch(ctx)
        if not project_branch:
            return True  # No project_id — nothing to do, treat as done
        worktree = ctx.task_dir / "worktree"
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", f"origin/{project_branch}", "HEAD"],
            cwd=worktree, capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0

    def pre_check(self, ctx: StepContext) -> bool:
        """Fetch project branch then check if HEAD is already a descendant."""
        project_branch = self._get_project_branch(ctx)
        if not project_branch:
            logger.debug("rebase_on_project_branch: no project_id on task, skipping")
            return True  # Skip — nothing to do
        worktree = ctx.task_dir / "worktree"
        subprocess.run(
            ["git", "fetch", "origin", project_branch],
            cwd=worktree, capture_output=True, text=True, timeout=60,
        )
        return self.check_done(ctx)

    def execute(self, ctx: StepContext) -> None:
        project_id = ctx.task.get("project_id")
        if not project_id:
            logger.debug("rebase_on_project_branch: no project_id on task, skipping")
            return

        project_branch = self._get_project_branch(ctx)
        worktree = ctx.task_dir / "worktree"

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
            subprocess.run(["git", "rebase", "--abort"], cwd=worktree, capture_output=True, text=True)
            raise RuntimeError(
                f"rebase_on_project_branch: git rebase failed:\n{rebase.stdout}\n{rebase.stderr}"
            )

        logger.info(f"rebase_on_project_branch: rebased onto origin/{project_branch}")

    def verify(self, ctx: StepContext) -> None:
        if not self.check_done(ctx):
            project_branch = self._get_project_branch(ctx)
            raise StepVerificationError(
                f"rebase_on_project_branch verify failed: HEAD is not a descendant of "
                f"origin/{project_branch} after rebase"
            )


rebase_on_project_branch = STEP_REGISTRY["rebase_on_project_branch"]


@register_step("create_project_pr")
class _CreateProjectPrStep(Step):
    """Create a PR for a project's shared branch. Stores PR metadata on the project.

    This step is used in project flows (not task flows). ctx.task is a project
    object (has 'id', 'title', 'branch', 'base_branch'); ctx.task_dir is the
    parent project root directory used for gh CLI operations.

    pre_check: PR already exists for this branch? Store metadata and skip.
    check_done: PR exists for project branch on GitHub.
    verify: PR exists and pr_number is stored on the project.
    """

    def _get_cwd(self, ctx: StepContext) -> Path:
        from .config import find_parent_project
        return ctx.task_dir if (ctx.task_dir and ctx.task_dir != Path(".")) else find_parent_project()

    def check_done(self, ctx: StepContext) -> bool:
        """Check if a PR already exists for the project branch."""
        project_branch = ctx.task.get("branch")
        if not project_branch:
            return False
        cwd = self._get_cwd(ctx)
        result = subprocess.run(
            ["gh", "pr", "view", project_branch, "--json", "number"],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        try:
            data = json.loads(result.stdout)
            return bool(data.get("number"))
        except json.JSONDecodeError:
            return False

    def pre_check(self, ctx: StepContext) -> bool:
        """If PR already exists, store its metadata on the project and skip execute."""
        project_branch = ctx.task.get("branch")
        if not project_branch:
            return False
        cwd = self._get_cwd(ctx)
        result = subprocess.run(
            ["gh", "pr", "view", project_branch, "--json", "number,url"],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        try:
            data = json.loads(result.stdout)
            pr_number = data.get("number")
            pr_url = data.get("url")
            if pr_number:
                from .sdk import get_sdk
                sdk = get_sdk()
                update_kwargs: dict = {}
                if pr_url:
                    update_kwargs["pr_url"] = pr_url
                update_kwargs["pr_number"] = pr_number
                try:
                    sdk.projects.update(ctx.task["id"], **update_kwargs)
                except Exception as e:
                    logger.warning(f"create_project_pr pre_check: failed to store PR metadata: {e}")
                logger.info(
                    f"create_project_pr pre_check: PR #{pr_number} already exists for "
                    f"{ctx.task['id']}, skipping"
                )
                return True
        except json.JSONDecodeError as e:
            logger.warning(f"create_project_pr pre_check: error checking PR: {e}")
        return False

    def execute(self, ctx: StepContext) -> None:
        from .config import get_base_branch
        from .sdk import get_sdk

        project = ctx.task
        project_id = project["id"]
        project_title = project.get("title", project_id)
        project_branch = project.get("branch")

        if not project_branch:
            raise RuntimeError(f"create_project_pr: project {project_id} has no branch")

        base_branch = project.get("base_branch") or get_base_branch()
        cwd = self._get_cwd(ctx)

        pr_body = (
            f"## Project: {project_title}\n\n"
            f"All child tasks for project `{project_id}` are complete. "
            f"This PR merges the shared project branch into `{base_branch}`."
        )

        pr_url: str | None = None
        pr_number: int | None = None

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
                # Race: PR was created between pre_check and execute — fetch it
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

    def verify(self, ctx: StepContext) -> None:
        if not self.check_done(ctx):
            raise StepVerificationError(
                "create_project_pr verify failed: PR not found on GitHub after creation"
            )
        from .sdk import get_sdk
        sdk = get_sdk()
        project = sdk.projects.get(ctx.task["id"])
        if not project or not project.get("pr_number"):
            raise StepVerificationError(
                "create_project_pr verify failed: pr_number not stored on project after PR creation"
            )


create_project_pr = STEP_REGISTRY["create_project_pr"]


@register_step("update_changelog")
class _UpdateChangelogStep(Step):
    """Read changes.md from task runtime dir and prepend to CHANGELOG.md on main.

    No pre_check or verify — this step is best-effort and non-fatal after merge.
    Skips silently if changes.md does not exist or is empty.
    """

    def execute(self, ctx: StepContext) -> None:
        from .config import find_parent_project, get_base_branch

        task = ctx.task
        task_dir = ctx.task_dir
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


update_changelog = STEP_REGISTRY["update_changelog"]


@register_step("aggregate_child_changes")
class _AggregateChildChangesStep(Step):
    """Aggregate changes.md files from all child tasks into task_dir/changes.md.

    Used in project flows: reads each child task's changes.md from its runtime
    directory and concatenates them into task_dir/changes.md so that the
    subsequent update_changelog step can process them as normal.

    Skips silently if no child tasks exist or none have a changes.md file.

    check_done: output changes.md exists in task_dir and is non-empty.
    verify: default no-op (step is safe to retry; empty output is a valid outcome).
    """

    def check_done(self, ctx: StepContext) -> bool:
        """Check if the output changes.md exists and has content."""
        output_file = ctx.task_dir / "changes.md"
        if not output_file.exists():
            return False
        return bool(output_file.read_text().strip())

    def execute(self, ctx: StepContext) -> None:
        from .config import get_tasks_dir
        from .sdk import get_sdk

        project_id = ctx.task.get("id")
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
            logger.debug(
                f"aggregate_child_changes: no child changes.md files found for {project_id}, skipping"
            )
            return

        output_file = ctx.task_dir / "changes.md"
        output_file.write_text("\n\n".join(aggregated_parts) + "\n")
        logger.info(
            f"aggregate_child_changes: aggregated {len(aggregated_parts)} child "
            f"changes.md file(s) for {project_id}"
        )


aggregate_child_changes = STEP_REGISTRY["aggregate_child_changes"]


@register_step("merge_project_pr")
class _MergeProjectPrStep(Step):
    """Merge the project's PR via gh CLI.

    Used in project flows for the 'provisional -> done' transition.
    Requires the project to have a 'pr_number' set (by create_project_pr).

    check_done: PR state is MERGED (prevents ghost completions).
    verify: PR state is MERGED after merge attempt.
    """

    def check_done(self, ctx: StepContext) -> bool:
        """Check if the project's PR is already in MERGED state."""
        pr_number = ctx.task.get("pr_number")
        if not pr_number:
            return False
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "state", "-q", ".state"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return False
        return result.stdout.strip().upper() == "MERGED"

    def execute(self, ctx: StepContext) -> None:
        from .config import find_parent_project

        project_id = ctx.task["id"]
        pr_number = ctx.task.get("pr_number")

        if not pr_number:
            raise RuntimeError(
                f"merge_project_pr: project {project_id} has no pr_number — "
                f"create_project_pr must run first"
            )

        cwd = ctx.task_dir if (ctx.task_dir and ctx.task_dir != Path(".")) else find_parent_project()

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

    def verify(self, ctx: StepContext) -> None:
        if not self.check_done(ctx):
            pr_number = ctx.task.get("pr_number")
            raise StepVerificationError(
                f"merge_project_pr verify failed: PR #{pr_number} not in MERGED state after merge attempt"
            )


merge_project_pr = STEP_REGISTRY["merge_project_pr"]
