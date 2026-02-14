"""Queue management with atomic operations and backpressure.

v2.0: API-only architecture. All task state operations use the Octopoid SDK.

IMPORTANT: Queue operations always happen in the MAIN REPO, not in agent worktrees.
This ensures queue state is centralized and not affected by git operations in worktrees.
"""

import os
import re
import subprocess
import socket
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import yaml

from .config import (
    ACTIVE_QUEUES,
    TaskQueue,
    get_queue_dir,
    get_queue_limits,
    get_orchestrator_dir,
    get_tasks_file_dir,
)
from .git_utils import cleanup_task_worktree
from .lock_utils import locked

# Global SDK instance (lazy-initialized)
_sdk: Optional[Any] = None


def get_sdk():
    """Get or initialize SDK client for API operations.

    The server URL is resolved in this order:
    1. OCTOPOID_SERVER_URL env var (useful for tests and CI)
    2. .octopoid/config.yaml server.url

    Returns:
        OctopoidSDK instance

    Raises:
        RuntimeError: If SDK not installed or server not configured
    """
    global _sdk

    if _sdk is not None:
        return _sdk

    try:
        from octopoid_sdk import OctopoidSDK
    except ImportError:
        raise RuntimeError(
            "octopoid-sdk not installed. Install with: pip install octopoid-sdk"
        )

    # Check env var override first (tests, CI, Docker)
    env_url = os.environ.get("OCTOPOID_SERVER_URL")
    if env_url:
        api_key = os.getenv("OCTOPOID_API_KEY")
        _sdk = OctopoidSDK(server_url=env_url, api_key=api_key)
        return _sdk

    # Load server configuration from config file
    try:
        import yaml
        orchestrator_dir = get_orchestrator_dir()
        config_path = orchestrator_dir.parent / ".octopoid" / "config.yaml"

        if not config_path.exists():
            raise RuntimeError(
                f"No .octopoid/config.yaml found. Run: octopoid init --server <url>"
            )

        with open(config_path) as f:
            config = yaml.safe_load(f)

        server_config = config.get("server", {})
        if not server_config.get("enabled"):
            raise RuntimeError(
                "Server not enabled in .octopoid/config.yaml"
            )

        server_url = server_config.get("url")
        if not server_url:
            raise RuntimeError(
                "Server URL not configured in .octopoid/config.yaml"
            )

        api_key = server_config.get("api_key") or os.getenv("OCTOPOID_API_KEY")

        _sdk = OctopoidSDK(server_url=server_url, api_key=api_key)
        return _sdk

    except Exception as e:
        raise RuntimeError(f"Failed to initialize SDK: {e}")


def get_orchestrator_id() -> str:
    """Get unique orchestrator instance ID.

    Returns:
        Orchestrator ID in format: {cluster}-{machine_id}
    """
    import yaml
    from .config import get_orchestrator_dir

    try:
        orchestrator_dir = get_orchestrator_dir()
        config_path = orchestrator_dir.parent / ".octopoid" / "config.yaml"

        if not config_path.exists():
            # Fallback to hostname if no config
            return socket.gethostname()

        with open(config_path) as f:
            config = yaml.safe_load(f)

        server_config = config.get("server", {})
        cluster = server_config.get("cluster", "default")
        machine_id = server_config.get("machine_id", socket.gethostname())

        return f"{cluster}-{machine_id}"
    except Exception:
        # Fallback to hostname on any error
        return socket.gethostname()


def get_queue_subdir(subdir: str) -> Path:
    """Get a specific queue subdirectory.

    Args:
        subdir: One of 'incoming', 'claimed', 'done', 'failed'

    Returns:
        Path to the subdirectory
    """
    queue_dir = get_queue_dir()
    path = queue_dir / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


# All queue directories that find_task_file searches
ALL_QUEUE_DIRS = [
    "incoming", "claimed", "provisional", "done", "failed",
    "rejected", "escalated", "recycled", "breakdown",
    "needs_continuation",
]


def find_task_file(task_id: str) -> Path | None:
    """Find a task's markdown file in the tasks directory.

    All task files live in .octopoid/tasks/. Also checks legacy queue
    subdirectories for backward compatibility with pre-migration files.

    Args:
        task_id: Task identifier (e.g. "9f5cda4b")

    Returns:
        Full Path to the task file, or None if not found
    """
    # Primary location: single tasks directory
    tasks_dir = get_tasks_file_dir()
    for pattern in [f"TASK-{task_id}.md", f"*{task_id}*.md"]:
        for candidate in tasks_dir.glob(pattern):
            if candidate.exists():
                return candidate

    # Legacy fallback: search queue subdirectories
    queue_dir = get_queue_dir()
    for subdir in ALL_QUEUE_DIRS:
        candidate = queue_dir / subdir / f"TASK-{task_id}.md"
        if candidate.exists():
            return candidate

    return None


def get_projects_dir() -> Path:
    """Get the projects directory.

    Returns:
        Path to .octopoid/shared/projects/
    """
    projects_dir = get_orchestrator_dir() / "shared" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    return projects_dir


def count_queue(subdir: str) -> int:
    """Count tasks in a queue via API.

    Args:
        subdir: One of 'incoming', 'claimed', 'done', 'failed', 'provisional'

    Returns:
        Number of tasks
    """
    try:
        tasks = list_tasks(subdir)  # Already uses SDK
        return len(tasks)
    except Exception as e:
        print(f"Warning: Failed to count queue {subdir}: {e}")
        return 0


def _get_pr_cache_path() -> Path:
    """Get path to PR count cache file."""
    return get_queue_dir() / ".pr_cache.json"


def count_open_prs(author: str | None = None, cache_seconds: int = 60) -> int:
    """Count open pull requests via gh CLI with file-based caching.

    Args:
        author: Optional author to filter by (e.g., '@me' or username)
        cache_seconds: How long to cache the result (default 60s)

    Returns:
        Number of open PRs (0 if gh command fails)
    """
    import json

    cache_path = _get_pr_cache_path()

    # Check cache
    try:
        if cache_path.exists():
            cache_data = json.loads(cache_path.read_text())
            cached_time = datetime.fromisoformat(cache_data.get("timestamp", ""))
            if (datetime.now() - cached_time).total_seconds() < cache_seconds:
                return cache_data.get("count", 0)
    except (json.JSONDecodeError, ValueError, KeyError):
        pass  # Cache invalid, fetch fresh

    # Fetch from GitHub
    try:
        cmd = ["gh", "pr", "list", "--state", "open", "--json", "number"]
        if author:
            cmd.extend(["--author", author])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return 0

        prs = json.loads(result.stdout)
        count = len(prs)

        # Update cache
        cache_path.write_text(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "count": count,
        }))

        return count
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError):
        return 0


def can_create_task() -> tuple[bool, str]:
    """Check if a new task can be created (backpressure check).

    Returns:
        Tuple of (can_create, reason_if_not)
    """
    limits = get_queue_limits()

    incoming = count_queue("incoming")
    claimed = count_queue("claimed")
    total_pending = incoming + claimed

    if total_pending >= limits["max_incoming"]:
        return False, f"Queue full: {total_pending} pending tasks (limit: {limits['max_incoming']})"

    return True, ""


def can_claim_task() -> tuple[bool, str]:
    """Check if a task can be claimed (backpressure check).

    Returns:
        Tuple of (can_claim, reason_if_not)
    """
    limits = get_queue_limits()

    incoming = count_queue("incoming")
    if incoming == 0:
        return False, "No tasks in incoming queue"

    claimed = count_queue("claimed")
    if claimed >= limits["max_claimed"]:
        return False, f"Too many claimed tasks: {claimed} (limit: {limits['max_claimed']})"

    open_prs = count_open_prs()
    if open_prs >= limits["max_open_prs"]:
        return False, f"Too many open PRs: {open_prs} (limit: {limits['max_open_prs']})"

    return True, ""


def list_tasks(subdir: str) -> list[dict[str, Any]]:
    """List tasks in a queue from the API server.

    Args:
        subdir: Queue name ('incoming', 'claimed', 'done', 'failed', 'provisional')

    Returns:
        List of task dictionaries sorted by priority and creation time
    """
    try:
        sdk = get_sdk()
        tasks = sdk.tasks.list(queue=subdir)

        # Sort by: 1) expedite flag (expedited first), 2) priority (P0 first), 3) created time
        priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        tasks.sort(key=lambda t: (
            0 if t.get("expedite") else 1,  # Expedited tasks first
            priority_order.get(t.get("priority", "P2"), 2),
            t.get("created_at") or t.get("created") or "",
        ))

        return tasks
    except Exception as e:
        print(f"Warning: Failed to list tasks in queue {subdir}: {e}")
        return []


def parse_task_file(task_path: Path) -> dict[str, Any] | None:
    """Parse a task file and extract metadata.

    Args:
        task_path: Path to the task .md file

    Returns:
        Dictionary with task metadata or None if invalid
    """
    try:
        content = task_path.read_text()
    except IOError:
        return None

    # Extract task ID from title
    title_match = re.search(r"^#\s*\[TASK-([^\]]+)\]\s*(.+)$", content, re.MULTILINE)
    task_id = title_match.group(1) if title_match else task_path.stem
    title = title_match.group(2).strip() if title_match else task_path.stem

    # Extract fields
    role_match = re.search(r"^ROLE:\s*(.+)$", content, re.MULTILINE)
    priority_match = re.search(r"^PRIORITY:\s*(.+)$", content, re.MULTILINE)
    branch_match = re.search(r"^BRANCH:\s*(.+)$", content, re.MULTILINE)
    created_match = re.search(r"^CREATED:\s*(.+)$", content, re.MULTILINE)
    created_by_match = re.search(r"^CREATED_BY:\s*(.+)$", content, re.MULTILINE)
    blocked_by_match = re.search(r"^BLOCKED_BY:\s*(.+)$", content, re.MULTILINE)
    checks_match = re.search(r"^CHECKS:\s*(.+)$", content, re.MULTILINE)
    breakdown_depth_match = re.search(r"^BREAKDOWN_DEPTH:\s*(\d+)$", content, re.MULTILINE)

    # Parse checks into a list
    checks: list[str] = []
    if checks_match:
        checks = [c.strip() for c in checks_match.group(1).strip().split(",") if c.strip()]

    # Task options
    skip_pr_match = re.search(r"^SKIP_PR:\s*(.+)$", content, re.MULTILINE)
    expedite_match = re.search(r"^EXPEDITE:\s*(.+)$", content, re.MULTILINE)

    # Continuation-related fields
    wip_branch_match = re.search(r"^WIP_BRANCH:\s*(.+)$", content, re.MULTILINE)
    last_agent_match = re.search(r"^LAST_AGENT:\s*(.+)$", content, re.MULTILINE)
    continuation_reason_match = re.search(r"^CONTINUATION_REASON:\s*(.+)$", content, re.MULTILINE)

    # Parse boolean fields (true/yes/1 are truthy)
    def parse_bool(match):
        if not match:
            return False
        val = match.group(1).strip().lower()
        return val in ("true", "yes", "1")

    return {
        "path": task_path,
        "id": task_id,
        "title": title,
        "role": role_match.group(1).strip() if role_match else None,
        "priority": priority_match.group(1).strip() if priority_match else "P2",
        "branch": branch_match.group(1).strip() if branch_match else "main",
        "created": created_match.group(1).strip() if created_match else None,
        "created_by": created_by_match.group(1).strip() if created_by_match else None,
        "blocked_by": blocked_by_match.group(1).strip() if blocked_by_match else None,
        "checks": checks,
        "breakdown_depth": int(breakdown_depth_match.group(1)) if breakdown_depth_match else 0,
        "skip_pr": parse_bool(skip_pr_match),
        "expedite": parse_bool(expedite_match),
        "wip_branch": wip_branch_match.group(1).strip() if wip_branch_match else None,
        "last_agent": last_agent_match.group(1).strip() if last_agent_match else None,
        "continuation_reason": continuation_reason_match.group(1).strip() if continuation_reason_match else None,
        "content": content,
    }


def resolve_task_file(filename: str) -> Path:
    """Resolve a task filename to its absolute path.

    The API stores just the filename. Task files all live in one directory.
    Legacy paths (relative or absolute) are also handled for backward
    compatibility — the basename is extracted and looked up in the tasks dir.

    Args:
        filename: Filename from the API (e.g., 'gh-8-2a4ad137.md')

    Returns:
        Absolute path to the task file

    Raises:
        FileNotFoundError: If the file doesn't exist in the tasks directory
    """
    # Extract basename in case API has a legacy full/relative path
    basename = Path(filename).name
    fp = get_tasks_file_dir() / basename
    if not fp.exists():
        raise FileNotFoundError(
            f"Task file not found: {fp} (API file_path={filename!r}). "
            f"The API is the source of truth for file_path — if the file "
            f"is missing, the task was created with a bad filename or the "
            f"file was deleted."
        )
    return fp


def claim_task(
    role_filter: str | None = None,
    agent_name: str | None = None,
    from_queue: str = "incoming",
    type_filter: str | None = None,
) -> dict[str, Any] | None:
    """Atomically claim a task from the API server.

    The API server handles atomic claiming with lease-based coordination,
    preventing race conditions across distributed orchestrators.

    After claiming, reads the task file from disk to populate 'content',
    since the API stores the filename but not file content.

    Args:
        role_filter: Only claim tasks with this role (e.g., 'implement', 'test', 'breakdown')
        agent_name: Name of claiming agent (for logging in task)
        from_queue: Queue to claim from (default 'incoming')
        type_filter: Only claim tasks with this type (e.g., 'product', 'infrastructure')

    Returns:
        Task info dictionary (with 'content' from file) if claimed, None if no task

    Raises:
        FileNotFoundError: If the claimed task's file doesn't exist on disk
    """
    sdk = get_sdk()
    orchestrator_id = get_orchestrator_id()
    limits = get_queue_limits()

    # Claim via API (atomic operation with lease)
    # Server enforces max_claimed to prevent races between agents
    task = sdk.tasks.claim(
        orchestrator_id=orchestrator_id,
        agent_name=agent_name or "unknown",
        role_filter=role_filter,
        type_filter=type_filter,
        max_claimed=limits.get("max_claimed"),
    )

    if task is None:
        return None

    # Resolve filename → absolute path and read content.
    # The API stores the filename; all task files live in .octopoid/tasks/.
    file_path_str = task.get("file_path")
    if file_path_str:
        fp = resolve_task_file(file_path_str)
        task["file_path"] = str(fp)  # Absolute path for downstream code
        parsed = parse_task_file(fp)
        if parsed:
            task["content"] = parsed["content"]

    return task


def unclaim_task(task_path: Path | str) -> None:
    """Return a claimed task to the incoming queue via API.

    Used when a system-level error (not a task error) prevents work,
    e.g. Claude dies instantly due to auth issues. The task is returned
    cleanly so another agent can pick it up later.

    Args:
        task_path: Path to the claimed task file
    """
    task_path = Path(task_path)

    try:
        task_info = parse_task_file(task_path)
        if not task_info:
            print(f"Warning: Could not parse task file {task_path}")
            return

        task_id = task_info["id"]
        sdk = get_sdk()
        sdk.tasks.update(task_id, queue="incoming", claimed_by=None)
    except Exception as e:
        print(f"Warning: Failed to unclaim task: {e}")


def complete_task(task_path: Path | str, result: str | None = None) -> Path:
    """Move a task to the done queue via API.

    Note: This directly marks a task as done. For tasks requiring review,
    use submit_completion() instead to go through the provisional queue.

    Args:
        task_path: Path to the claimed task file
        result: Optional result summary to append

    Returns:
        Path to task file (queue change handled by API)
    """
    task_path = Path(task_path)

    try:
        # Get task ID from file
        task_info = parse_task_file(task_path)
        if not task_info:
            print(f"Warning: Could not parse task file {task_path}")
            return task_path

        task_id = task_info["id"]

        # Accept completion via API
        sdk = get_sdk()
        sdk.tasks.accept(task_id, accepted_by="complete_task")

        # Append completion info to local file
        with open(task_path, "a") as f:
            f.write(f"\nCOMPLETED_AT: {datetime.now().isoformat()}\n")
            if result:
                f.write(f"\n## Result\n{result}\n")

        # Clean up agent notes
        cleanup_task_notes(task_id)

        return task_path
    except Exception as e:
        print(f"Warning: Failed to complete task: {e}")
        return task_path


def _generate_execution_notes(
    task_info: dict,
    commits_count: int,
    turns_used: int | None = None,
) -> str:
    """Generate execution notes summarizing what was done.

    Args:
        task_info: Parsed task information
        commits_count: Number of commits made
        turns_used: Number of Claude turns used

    Returns:
        Execution notes string (concise summary)
    """
    parts = []

    # Commit summary
    if commits_count > 0:
        parts.append(f"Created {commits_count} commit{'s' if commits_count != 1 else ''}")
    else:
        parts.append("No commits made")

    # Turn usage
    if turns_used:
        parts.append(f"{turns_used} turn{'s' if turns_used != 1 else ''} used")

    # Try to get commit messages from git log (if in a repo)
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-n", str(min(commits_count, 5))],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            commit_summary = result.stdout.strip().replace("\n", "; ")
            parts.append(f"Changes: {commit_summary}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return ". ".join(parts) + "."


def submit_completion(
    task_path: Path | str,
    commits_count: int = 0,
    turns_used: int | None = None,
) -> Path | None:
    """Submit a task for review via API (moves to provisional queue).

    The API server handles state transition to provisional queue.
    Auto-rejects 0-commit submissions from previously-claimed tasks.

    Args:
        task_path: Path to the claimed task file
        commits_count: Number of commits made during implementation
        turns_used: Number of Claude turns used

    Returns:
        Path to the task file (queue change handled by API)
    """
    task_path = Path(task_path)

    try:
        # Get task ID from file
        task_info = parse_task_file(task_path)
        if not task_info:
            print(f"Warning: Could not parse task file {task_path}")
            return None

        task_id = task_info["id"]

        # Get current task state from API
        sdk = get_sdk()
        task = sdk.tasks.get(task_id)
        if not task:
            print(f"Warning: Task {task_id} not found in API")
            return None

        # Auto-reject 0-commit submissions from previously-claimed tasks
        attempt_count = task.get("attempt_count", 0)
        rejection_count = task.get("rejection_count", 0)
        previously_claimed = attempt_count > 0 or rejection_count > 0

        if commits_count == 0 and previously_claimed:
            return reject_completion(
                task_path,
                reason="No commits made. Read the task file and rejection feedback, then implement the required changes.",
                accepted_by="submit_completion",
            )

        # Generate execution notes
        execution_notes = _generate_execution_notes(
            task_info, commits_count, turns_used
        )

        # Submit via API (moves to provisional queue)
        sdk.tasks.submit(
            task_id=task_id,
            commits_count=commits_count,
            turns_used=turns_used or 0,
            execution_notes=execution_notes,
        )

        # Append submission metadata to local file for human readability
        with open(task_path, "a") as f:
            f.write(f"\nSUBMITTED_AT: {datetime.now().isoformat()}\n")
            f.write(f"COMMITS_COUNT: {commits_count}\n")
            if turns_used:
                f.write(f"TURNS_USED: {turns_used}\n")
            f.write(f"EXECUTION_NOTES: {execution_notes}\n")

        return task_path

    except Exception as e:
        print(f"Warning: Failed to submit completion for {task_path}: {e}")
        return None


def accept_completion(
    task_path: Path | str,
    accepted_by: str | None = None,
) -> Path:
    """Accept a provisional task via API (moves to done queue).

    Called by the pre-check or gatekeeper when a task passes.
    The API server handles state transition to done queue.

    Args:
        task_path: Path to the provisional task file
        accepted_by: Name of the agent or system that accepted (e.g. "scheduler", "gatekeeper", "human")

    Returns:
        Path to task file (queue change handled by API)
    """
    task_path = Path(task_path)

    try:
        # Get task ID from file
        task_info = parse_task_file(task_path)
        if not task_info:
            print(f"Warning: Could not parse task file {task_path}")
            return task_path

        task_id = task_info["id"]

        # Accept via API (moves to done queue)
        sdk = get_sdk()
        sdk.tasks.accept(task_id=task_id, accepted_by=accepted_by)

        # Append acceptance metadata to local file
        if task_path.exists():
            with open(task_path, "a") as f:
                f.write(f"\nACCEPTED_AT: {datetime.now().isoformat()}\n")
                if accepted_by:
                    f.write(f"ACCEPTED_BY: {accepted_by}\n")

        # Clean up agent notes
        cleanup_task_notes(task_id)

        # Clean up ephemeral task worktree (push commits, delete worktree)
        cleanup_task_worktree(task_id, push_commits=True)

        return task_path

    except Exception as e:
        print(f"Warning: Failed to accept completion for {task_path}: {e}")
        return task_path


def reject_completion(
    task_path: Path | str,
    reason: str,
    accepted_by: str | None = None,
) -> Path:
    """Reject a provisional task via API (moves back to incoming for retry).

    Called by the pre-check when a task fails (e.g., no commits).
    The API server increments attempt_count and moves to incoming queue.

    Args:
        task_path: Path to the provisional task file
        reason: Rejection reason
        accepted_by: Name of the agent or system that rejected

    Returns:
        Path to task file (queue change handled by API)
    """
    task_path = Path(task_path)

    try:
        # Get task ID from file
        task_info = parse_task_file(task_path)
        if not task_info:
            print(f"Warning: Could not parse task file {task_path}")
            return task_path

        task_id = task_info["id"]

        # Reject via API (moves to incoming queue, increments attempt_count)
        sdk = get_sdk()
        updated_task = sdk.tasks.reject(
            task_id=task_id,
            reason=reason,
            rejected_by=accepted_by
        )

        attempt_count = updated_task.get("attempt_count", 0)

        # Append rejection metadata to local file
        with open(task_path, "a") as f:
            f.write(f"\nREJECTED_AT: {datetime.now().isoformat()}\n")
            f.write(f"REJECTION_REASON: {reason}\n")
            f.write(f"ATTEMPT_COUNT: {attempt_count}\n")
            if accepted_by:
                f.write(f"REJECTED_BY: {accepted_by}\n")

        # Clean up ephemeral task worktree (worktree is deleted; next attempt gets fresh checkout)
        cleanup_task_worktree(task_id, push_commits=True)

        return task_path

    except Exception as e:
        print(f"Warning: Failed to reject completion for {task_path}: {e}")
        return task_path


def _insert_rejection_feedback(content: str, feedback_section: str) -> str:
    """Insert rejection feedback after the metadata block, before ## Context.

    The metadata block consists of lines like ROLE:, PRIORITY:, BRANCH:, etc.
    that appear between the title (# [TASK-...]) and the first ## heading.

    On repeated rejections, any existing rejection notice is replaced so only
    the latest feedback appears.

    Args:
        content: Original task file content
        feedback_section: The formatted rejection notice to insert

    Returns:
        Content with rejection feedback inserted after metadata
    """
    # Strip any existing rejection notice section (new format)
    content = re.sub(
        r'\n*## Rejection Notice.*?(?=\n## |\Z)',
        '',
        content,
        flags=re.DOTALL,
    )

    # Also strip old-style "## Review Feedback" sections (from before this change)
    content = re.sub(
        r'\n*## Review Feedback \(rejection #\d+\).*?(?=\n## |\Z)',
        '',
        content,
        flags=re.DOTALL,
    )

    # Find where to insert: before the first ## heading
    lines = content.split('\n')
    insert_idx = None

    for i, line in enumerate(lines):
        if line.startswith('## '):
            insert_idx = i
            break

    if insert_idx is not None:
        feedback_lines = feedback_section.rstrip('\n').split('\n')
        lines = lines[:insert_idx] + feedback_lines + ['', ''] + lines[insert_idx:]
    else:
        # No ## heading found — append at the end
        lines.append('')
        lines.extend(feedback_section.rstrip('\n').split('\n'))

    return '\n'.join(lines)


def review_reject_task(
    task_path: Path | str,
    feedback: str,
    rejected_by: str | None = None,
    max_rejections: int = 3,
) -> tuple[Path, str]:
    """Reject a provisional task with review feedback from gatekeepers.

    Increments rejection_count (distinct from attempt_count used by pre-check).
    If rejection_count reaches max_rejections, escalates to human attention
    instead of cycling back to the implementer.

    The task's branch is preserved so the implementer can push fixes.
    Rejection feedback is inserted near the top of the task file (after the
    metadata block, before ## Context) so agents see it immediately.

    The file stays in .octopoid/tasks/ — only the API queue state changes.

    Args:
        task_path: Path to the task file
        feedback: Aggregated review feedback markdown
        rejected_by: Name of the reviewer/coordinator
        max_rejections: Maximum rejections before escalation (default 3)

    Returns:
        Tuple of (task_path, action) where action is 'rejected' or 'escalated'
    """
    task_path = Path(task_path)
    task_info = parse_task_file(task_path)
    task_id = task_info["id"] if task_info else None

    # Get current rejection count from API
    rejection_count = 0
    if task_id:
        try:
            sdk = get_sdk()
            api_task = sdk.tasks.get(task_id)
            if api_task:
                rejection_count = (api_task.get("rejection_count") or 0) + 1
        except Exception:
            pass

    escalated = rejection_count >= max_rejections

    # Insert feedback into the task file (file stays in place)
    original_content = task_path.read_text()

    feedback_section = f"## Rejection Notice (rejection #{rejection_count})\n\n"
    feedback_section += "**WARNING: This task was previously attempted but the work was rejected.**\n"
    feedback_section += "**Existing code on the branch does NOT satisfy the acceptance criteria.**\n"
    feedback_section += "**You MUST make new commits to address the feedback below.**\n\n"
    feedback_section += f"{feedback}\n\n"
    feedback_section += f"REVIEW_REJECTED_AT: {datetime.now().isoformat()}\n"
    if rejected_by:
        feedback_section += f"REVIEW_REJECTED_BY: {rejected_by}\n"

    new_content = _insert_rejection_feedback(original_content, feedback_section)
    task_path.write_text(new_content)

    # Update API state — queue changes, file stays put
    if task_id:
        try:
            sdk = get_sdk()
            if escalated:
                sdk.tasks.update(task_id, queue="escalated", claimed_by=None)
            else:
                sdk.tasks.reject(
                    task_id=task_id,
                    reason=feedback[:500],
                    rejected_by=rejected_by,
                )
        except Exception as e:
            print(f"Warning: Failed to update task {task_id} via API: {e}")

    if escalated:
        from . import message_utils
        message_utils.warning(
            f"Task {task_id or task_path.stem} escalated after {rejection_count} rejections",
            f"Task has been rejected {rejection_count} times by reviewers. "
            f"Human attention required.\n\nLatest feedback:\n{feedback[:1000]}",
            rejected_by or "gatekeeper",
            task_id,
        )

    action = "escalated" if escalated else "rejected"

    # Clean up ephemeral task worktree (task will be retried or escalated)
    if task_id:
        cleanup_task_worktree(task_id, push_commits=True)

    return (task_path, action)


def get_review_feedback(task_id: str) -> str | None:
    """Extract review feedback sections from a task's markdown file.

    Supports both the new '## Rejection Notice' format (inserted near top)
    and the legacy '## Review Feedback' format (appended at bottom).

    Args:
        task_id: Task identifier

    Returns:
        Combined feedback text or None if no feedback found
    """
    task = get_task_by_id(task_id)
    if not task:
        return None

    content = task.get("content", "")
    if not content:
        return None

    # Try new format first: ## Rejection Notice
    new_sections = re.findall(
        r'## Rejection Notice.*?\n(.*?)(?=\n## |\Z)',
        content,
        re.DOTALL,
    )

    if new_sections:
        return "\n\n---\n\n".join(section.strip() for section in new_sections)

    # Fall back to legacy format: ## Review Feedback
    legacy_sections = re.findall(
        r'## Review Feedback \(rejection #\d+\)\s*\n(.*?)(?=\n## |\Z)',
        content,
        re.DOTALL,
    )

    if not legacy_sections:
        return None

    return "\n\n---\n\n".join(section.strip() for section in legacy_sections)


def escalate_to_planning(task_path: Path | str, plan_id: str) -> Path:
    """Escalate a failed task to planning.

    Creates a planning task to break down the original task into micro-tasks.
    Called when a task has exceeded max_attempts_before_planning.

    The file stays in .octopoid/tasks/ — only the API queue state changes.

    Args:
        task_path: Path to the task file being escalated
        plan_id: ID of the new planning task

    Returns:
        Path to the task file (unchanged)
    """
    task_path = Path(task_path)

    task_info = parse_task_file(task_path)
    if task_info:
        try:
            sdk = get_sdk()
            sdk.tasks.update(task_info["id"], queue="escalated")
        except Exception as e:
            print(f"Warning: Failed to escalate task via API: {e}")

    # Append escalation info to local file
    with open(task_path, "a") as f:
        f.write(f"\nESCALATED_AT: {datetime.now().isoformat()}\n")
        f.write(f"PLAN_ID: {plan_id}\n")

    return task_path


def fail_task(task_path: Path | str, error: str) -> Path:
    """Fail a task via API (moves to failed queue).

    Args:
        task_path: Path to the claimed task file
        error: Error message/description

    Returns:
        Path to task file (queue change handled by API)
    """
    task_path = Path(task_path)

    try:
        # Get task ID from file
        task_info = parse_task_file(task_path)
        if not task_info:
            print(f"Warning: Could not parse task file {task_path}")
            return task_path

        task_id = task_info["id"]

        # Fail via API (moves to failed queue)
        sdk = get_sdk()
        sdk.tasks.update(task_id, queue="failed")

        # Append error info to local file (truncated)
        error_summary = error[:500] + ("..." if len(error) > 500 else "")
        with open(task_path, "a") as f:
            f.write(f"\nFAILED_AT: {datetime.now().isoformat()}\n")
            f.write(f"\n## Error\n```\n{error_summary}\n```\n")

        # Clean up ephemeral task worktree
        cleanup_task_worktree(task_id, push_commits=False)

        return task_path

    except Exception as e:
        print(f"Warning: Failed to fail task {task_path}: {e}")
        return task_path


def reject_task(
    task_path: Path | str,
    reason: str,
    details: str | None = None,
    rejected_by: str | None = None,
) -> Path:
    """Reject a task and move it to the rejected queue via API.

    Use this when a task cannot or should not be completed, for example:
    - Functionality already exists (already_implemented)
    - Task is blocked by unmet dependencies (blocked)
    - Task doesn't make sense or is invalid (invalid_task)
    - Task duplicates another task (duplicate)
    - Task is out of scope for the agent (out_of_scope)

    Args:
        task_path: Path to the claimed task file
        reason: Rejection reason code (already_implemented, blocked, invalid_task, duplicate, out_of_scope)
        details: Detailed explanation of why the task is being rejected
        rejected_by: Name of the agent rejecting the task

    Returns:
        Path to task file (queue change handled by API)
    """
    task_path = Path(task_path)

    try:
        # Get task ID from file
        task_info = parse_task_file(task_path)
        if not task_info:
            print(f"Warning: Could not parse task file {task_path}")
            return task_path

        task_id = task_info["id"]

        # Update to rejected queue via API
        sdk = get_sdk()
        sdk.tasks.update(task_id, queue="rejected")

        # Append rejection info to local file
        with open(task_path, "a") as f:
            f.write(f"\nREJECTED_AT: {datetime.now().isoformat()}\n")
            f.write(f"REJECTION_REASON: {reason}\n")
            if rejected_by:
                f.write(f"REJECTED_BY: {rejected_by}\n")
            if details:
                f.write(f"\n## Rejection Details\n{details}\n")

        return task_path
    except Exception as e:
        print(f"Warning: Failed to reject task: {e}")
        return task_path


def retry_task(task_path: Path | str) -> Path:
    """Retry a failed task via API (moves back to incoming).

    Args:
        task_path: Path to the failed task file

    Returns:
        Path to task file (queue change handled by API)
    """
    task_path = Path(task_path)

    try:
        task_info = parse_task_file(task_path)
        if not task_info:
            return task_path

        sdk = get_sdk()
        sdk.tasks.update(task_info["id"], queue="incoming", claimed_by=None, claimed_at=None)

        with open(task_path, "a") as f:
            f.write(f"\nRETRIED_AT: {datetime.now().isoformat()}\n")

        return task_path
    except Exception as e:
        print(f"Warning: Failed to retry task: {e}")
        return task_path


def reset_task(task_id: str) -> dict[str, Any]:
    """Reset a task to incoming via API with clean state.

    Args:
        task_id: Task identifier (e.g. "9f5cda4b")

    Returns:
        Dict with 'task_id', 'action'

    Raises:
        RuntimeError: If the API update fails
    """
    try:
        sdk = get_sdk()

        # Get current task to find file path
        task = sdk.tasks.get(task_id)
        if not task:
            raise LookupError(f"Task {task_id} not found in API")

        old_queue = task.get("queue", "unknown")

        # Reset task state via API
        sdk.tasks.update(
            task_id,
            queue="incoming",
            claimed_by=None,
            claimed_at=None,
            checks=None,
            check_results=None,
            rejection_count=0,
        )

        # Append reset marker to local file for human readability
        if task.get("file_path"):
            file_path = Path(task["file_path"])
            if file_path.exists():
                try:
                    with open(file_path, "a") as f:
                        f.write(f"\nRESET_AT: {datetime.now().isoformat()}\n")
                        f.write(f"RESET_FROM: {old_queue}\n")
                except IOError:
                    pass

        return {
            "task_id": task_id,
            "old_queue": old_queue,
            "new_queue": "incoming",
            "action": "reset",
        }
    except Exception as e:
        raise RuntimeError(f"Failed to reset task {task_id}: {e}")


def hold_task(task_id: str) -> dict[str, Any]:
    """Park a task in the escalated queue via API so the scheduler ignores it.

    Args:
        task_id: Task identifier (e.g. "9f5cda4b")

    Returns:
        Dict with 'task_id', 'action'

    Raises:
        RuntimeError: If the API update fails
    """
    try:
        sdk = get_sdk()

        # Get current task to find file path
        task = sdk.tasks.get(task_id)
        if not task:
            raise LookupError(f"Task {task_id} not found in API")

        old_queue = task.get("queue", "unknown")

        # Move task to escalated queue via API
        sdk.tasks.update(
            task_id,
            queue="escalated",
            claimed_by=None,
            claimed_at=None,
            checks=None,
            check_results=None,
        )

        # Append hold marker to local file for human readability
        if task.get("file_path"):
            file_path = Path(task["file_path"])
            if file_path.exists():
                try:
                    with open(file_path, "a") as f:
                        f.write(f"\nHELD_AT: {datetime.now().isoformat()}\n")
                        f.write(f"HELD_FROM: {old_queue}\n")
                except IOError:
                    pass

        return {
            "task_id": task_id,
            "old_queue": old_queue,
            "new_queue": "escalated",
            "action": "held",
        }
    except Exception as e:
        raise RuntimeError(f"Failed to hold task {task_id}: {e}")


def mark_needs_continuation(
    task_path: Path | str,
    reason: str,
    branch_name: str | None = None,
    agent_name: str | None = None,
) -> Path:
    """Mark a task as needing continuation via API and move to needs_continuation queue.

    Use this when an agent exits before completing work (e.g., max turns reached).
    The task can be resumed by the same or another agent.

    Args:
        task_path: Path to the claimed task file
        reason: Why continuation is needed (e.g., "max_turns_reached", "uncommitted_changes")
        branch_name: Branch where work-in-progress exists
        agent_name: Agent that was working on the task

    Returns:
        Path to the task file (unchanged, local file only)
    """
    task_path = Path(task_path)

    try:
        # Get task ID from file
        task_info = parse_task_file(task_path)
        if not task_info:
            print(f"Warning: Could not parse task file {task_path}")
            return task_path

        task_id = task_info["id"]

        # Update queue via API
        sdk = get_sdk()
        sdk.tasks.update(task_id, queue="needs_continuation")

        # Append continuation info to local file
        with open(task_path, "a") as f:
            f.write(f"\nNEEDS_CONTINUATION_AT: {datetime.now().isoformat()}\n")
            f.write(f"CONTINUATION_REASON: {reason}\n")
            if branch_name:
                f.write(f"WIP_BRANCH: {branch_name}\n")
            if agent_name:
                f.write(f"LAST_AGENT: {agent_name}\n")

        return task_path
    except Exception as e:
        print(f"Warning: Failed to mark task for continuation: {e}")
        return task_path


def resume_task(task_path: Path | str, agent_name: str | None = None) -> Path:
    """Move a task from needs_continuation back to claimed via API for resumption.

    Args:
        task_path: Path to the needs_continuation task file
        agent_name: Agent resuming the task

    Returns:
        Path to the task file (unchanged, local file only)
    """
    task_path = Path(task_path)

    try:
        # Get task ID from file
        task_info = parse_task_file(task_path)
        if not task_info:
            print(f"Warning: Could not parse task file {task_path}")
            return task_path

        task_id = task_info["id"]

        # Update queue via API
        sdk = get_sdk()
        orchestrator_id = get_orchestrator_id()
        sdk.tasks.update(
            task_id,
            queue="claimed",
            claimed_by=agent_name or "unknown",
            orchestrator_id=orchestrator_id,
        )

        # Append resume info to local file
        with open(task_path, "a") as f:
            f.write(f"\nRESUMED_AT: {datetime.now().isoformat()}\n")
            if agent_name:
                f.write(f"RESUMED_BY: {agent_name}\n")

        return task_path
    except Exception as e:
        print(f"Warning: Failed to resume task: {e}")
        return task_path


def find_task_by_id(task_id: str, queues: list[str] | None = None) -> dict[str, Any] | None:
    """Find a task by its ID, optionally filtered by queue state.

    Fetches from the API and reads the task file from disk to populate
    'content', just like claim_task() does.

    Args:
        task_id: Task ID to find (e.g., "9f5cda4b")
        queues: Only return the task if it's in one of these queues
                (e.g., ["claimed", "needs_continuation"])

    Returns:
        Task info dict (with content) or None if not found / not in specified queues
    """
    task = get_task_by_id(task_id)

    if task is None:
        return None

    # Filter by queue state (API is the source of truth)
    if queues is not None:
        task_queue = task.get("queue")
        if task_queue not in queues:
            return None

    # Resolve filename → absolute path and read content from disk
    file_path_str = task.get("file_path")
    if file_path_str and "content" not in task:
        try:
            fp = resolve_task_file(file_path_str)
            task["file_path"] = str(fp)
            parsed = parse_task_file(fp)
            if parsed:
                task["content"] = parsed["content"]
        except FileNotFoundError:
            pass  # Task file missing — content stays empty

    return task


def get_continuation_tasks(agent_name: str | None = None) -> list[dict[str, Any]]:
    """Get tasks that need continuation, optionally filtered by agent.

    Args:
        agent_name: Filter to tasks last worked on by this agent

    Returns:
        List of tasks needing continuation
    """
    tasks = list_tasks("needs_continuation")

    if agent_name:
        # Filter to tasks that were being worked on by this agent
        filtered = []
        for task in tasks:
            content = task.get("content", "")
            if f"LAST_AGENT: {agent_name}" in content or f"CLAIMED_BY: {agent_name}" in content:
                filtered.append(task)
        return filtered

    return tasks


def create_task(
    title: str,
    role: str,
    context: str,
    acceptance_criteria: list[str] | str,
    priority: str = "P1",
    branch: str = "main",
    created_by: str = "human",
    blocked_by: str | None = None,
    project_id: str | None = None,
    queue: str = "incoming",
    checks: list[str] | None = None,
    breakdown_depth: int = 0,
) -> Path:
    """Create a new task file in the specified queue.

    Args:
        title: Task title
        role: Target role (implement, test, review, breakdown)
        context: Background/context section content
        acceptance_criteria: List of acceptance criteria lines, or a single
            string (which will be split on newlines). Lines already prefixed
            with "- [ ]" are kept as-is; bare lines get the prefix added.
        priority: P0, P1, or P2
        branch: Base branch to work from
        created_by: Who created the task
        blocked_by: Comma-separated list of task IDs that block this task
        project_id: Optional parent project ID
        queue: Queue to create in (default: incoming, can be 'breakdown')
        checks: Optional list of check names that must pass before human review
            (e.g. ['gk-testing-octopoid'])
        breakdown_depth: Number of breakdown levels deep (0 = original task)

    Returns:
        Path to created task file
    """
    task_id = uuid4().hex[:8]
    filename = f"TASK-{task_id}.md"

    # Normalize blocked_by: ensure None/empty/string-"None" all become None
    if not blocked_by or blocked_by == "None":
        blocked_by = None

    # Normalize acceptance_criteria to a list of lines
    if isinstance(acceptance_criteria, str):
        acceptance_criteria = [
            line for line in acceptance_criteria.splitlines() if line.strip()
        ]

    # Build markdown checklist, preserving existing "- [ ]" prefixes
    criteria_lines = []
    for c in acceptance_criteria:
        stripped = c.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
            criteria_lines.append(stripped)
        else:
            criteria_lines.append(f"- [ ] {stripped}")
    criteria_md = "\n".join(criteria_lines)

    blocked_by_line = f"BLOCKED_BY: {blocked_by}\n" if blocked_by else ""
    project_line = f"PROJECT: {project_id}\n" if project_id else ""
    checks_line = f"CHECKS: {','.join(checks)}\n" if checks else ""
    breakdown_depth_line = f"BREAKDOWN_DEPTH: {breakdown_depth}\n" if breakdown_depth > 0 else ""

    # All task files go in one directory — API owns the queue state
    task_path = get_tasks_file_dir() / filename

    content = f"""# [TASK-{task_id}] {title}

ROLE: {role}
PRIORITY: {priority}
BRANCH: {branch}
CREATED: {datetime.now().isoformat()}
CREATED_BY: {created_by}
{project_line}{blocked_by_line}{checks_line}{breakdown_depth_line}
## Context
{context}

## Acceptance Criteria
{criteria_md}
"""

    # Write local file
    task_path.write_text(content)

    # Resolve hooks from config for this task
    hooks_json = None
    try:
        from .hook_manager import HookManager
        hm = HookManager(sdk=get_sdk())
        hooks_list = hm.resolve_hooks_for_task(task_type=None)
        if hooks_list:
            import json as _json
            hooks_json = _json.dumps(hooks_list)
    except Exception as e:
        print(f"Warning: Failed to resolve hooks: {e}")

    # Register task with API server
    try:
        sdk = get_sdk()
        sdk.tasks.create(
            id=task_id,
            file_path=filename,
            title=title,
            role=role,
            priority=priority,
            context=context,
            acceptance_criteria="\n".join(criteria_lines),
            queue=queue,
            branch=branch,
            hooks=hooks_json,
            metadata={
                "created_by": created_by,
                "blocked_by": blocked_by,
                "project_id": project_id,
                "checks": checks,
                "breakdown_depth": breakdown_depth,
            }
        )
    except Exception as e:
        print(f"Warning: Failed to register task with API: {e}")
        # Still return task_path since local file was created
        # This allows offline task creation

    return task_path


def _get_agent_state_dir() -> Path | None:
    """Get the agent's state directory (outside worktree).

    Returns None if not running as an agent (e.g., scheduler context).
    """
    orchestrator_dir = os.environ.get("ORCHESTRATOR_DIR")
    agent_name = os.environ.get("AGENT_NAME")
    if orchestrator_dir and agent_name:
        return Path(orchestrator_dir) / "agents" / agent_name
    return None


def write_task_marker(task_id: str, task_path: Path) -> None:
    """Write a task marker file in the agent's state directory.

    This links the agent to a specific task, allowing detection
    of stale resume attempts (task completed but worktree not reset).

    The marker is stored OUTSIDE the worktree so it's not affected
    by git operations (reset, checkout, etc.).

    Args:
        task_id: Task ID being worked on
        task_path: Path to the task file
    """
    state_dir = _get_agent_state_dir()
    if not state_dir:
        return  # Not running as agent

    marker_path = state_dir / "current_task.json"
    marker_data = {
        "task_id": task_id,
        "task_path": str(task_path),
        "started_at": datetime.now().isoformat(),
    }
    import json
    state_dir.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(marker_data, indent=2))


def read_task_marker_for(agent_name: str) -> dict[str, Any] | None:
    """Read the task marker file for a specific agent.

    Args:
        agent_name: Name of the agent

    Returns:
        Task marker data or None if not present
    """
    marker_path = get_orchestrator_dir() / "agents" / agent_name / "current_task.json"
    if not marker_path.exists():
        return None

    try:
        import json
        return json.loads(marker_path.read_text())
    except (IOError, json.JSONDecodeError):
        return None


def clear_task_marker_for(agent_name: str) -> None:
    """Clear the task marker file for a specific agent.

    Args:
        agent_name: Name of the agent
    """
    marker_path = get_orchestrator_dir() / "agents" / agent_name / "current_task.json"
    if marker_path.exists():
        marker_path.unlink()


def read_task_marker() -> dict[str, Any] | None:
    """Read the task marker file from agent's state directory.

    Returns:
        Task marker data or None if not present
    """
    state_dir = _get_agent_state_dir()
    if not state_dir:
        return None

    agent_name = os.environ.get("AGENT_NAME")
    if not agent_name:
        return None

    return read_task_marker_for(agent_name)


def clear_task_marker() -> None:
    """Clear the task marker file from agent's state directory."""
    state_dir = _get_agent_state_dir()
    if not state_dir:
        return

    agent_name = os.environ.get("AGENT_NAME")
    if not agent_name:
        return

    clear_task_marker_for(agent_name)


def is_task_still_valid(task_id: str) -> bool:
    """Check if a task is still valid to work on.

    A task is valid if it's in an active queue (claimed or needs_continuation).
    If it's in a terminal queue (done, failed, rejected, etc.), it should not be resumed.

    Args:
        task_id: Task ID to check

    Returns:
        True if task can still be worked on
    """
    task = find_task_by_id(task_id, queues=ACTIVE_QUEUES)
    return task is not None


def get_queue_status() -> dict[str, Any]:
    """Get overall queue status for monitoring.

    Returns:
        Dictionary with queue counts and task lists
    """
    queues = ["incoming", "claimed", "needs_continuation", "done", "failed", "rejected",
              "breakdown", "provisional", "escalated"]

    result = {}
    for q in queues:
        tasks = list_tasks(q)
        result[q] = {
            "count": len(tasks),
            "tasks": tasks[-10:] if q in ("done", "rejected") else tasks,
        }

    result["limits"] = get_queue_limits()
    result["open_prs"] = count_open_prs()

    result["projects"] = {
        "draft": len(list_projects("draft")),
        "active": len(list_projects("active")),
        "ready-for-pr": len(list_projects("ready-for-pr")),
        "complete": len(list_projects("complete")),
    }

    return result


def get_task_by_id(task_id: str) -> dict[str, Any] | None:
    """Get a task by its ID from the API server.

    Args:
        task_id: Task identifier (e.g., 'abc12345')

    Returns:
        Task dict or None if not found
    """
    try:
        sdk = get_sdk()
        task = sdk.tasks.get(task_id)
        return task
    except Exception as e:
        # Log error but don't crash
        print(f"Warning: Failed to get task {task_id}: {e}")
        return None


# =============================================================================
# Project Operations
# =============================================================================


def create_project(
    title: str,
    description: str,
    created_by: str = "human",
    base_branch: str = "main",
    branch: str | None = None,
) -> dict[str, Any]:
    """Create a new project via API with local YAML file.

    Args:
        title: Project title
        description: Project description
        created_by: Who created the project
        base_branch: Base branch to create feature branch from
        branch: Feature branch name (auto-generated if not provided)

    Returns:
        Created project as dictionary
    """
    # Generate project ID
    project_id = f"PROJ-{uuid4().hex[:8]}"

    try:
        sdk = get_sdk()
        # Note: SDK projects API needs to be implemented in the SDK client
        # For now, create local YAML file only
        # TODO: Add sdk.projects.create() when server endpoint exists

        project = {
            "id": project_id,
            "title": title,
            "description": description,
            "status": "draft",
            "branch": branch,
            "base_branch": base_branch,
            "created_at": datetime.now().isoformat(),
            "created_by": created_by,
        }

        # Write YAML file for visibility
        _write_project_file(project)

        return project
    except Exception as e:
        print(f"Warning: Failed to create project: {e}")
        raise


def _write_project_file(project: dict[str, Any]) -> Path:
    """Write project data to YAML file.

    Args:
        project: Project dictionary

    Returns:
        Path to the YAML file
    """
    projects_dir = get_projects_dir()
    file_path = projects_dir / f"{project['id']}.yaml"

    # Convert to YAML-friendly format
    data = {
        "id": project["id"],
        "title": project["title"],
        "description": project.get("description"),
        "status": project.get("status", "draft"),
        "branch": project.get("branch"),
        "base_branch": project.get("base_branch", "main"),
        "created_at": project.get("created_at"),
        "created_by": project.get("created_by"),
        "completed_at": project.get("completed_at"),
    }

    with open(file_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    return file_path


def get_project(project_id: str) -> dict[str, Any] | None:
    """Get a project by ID via API.

    Args:
        project_id: Project identifier

    Returns:
        Project as dictionary or None if not found
    """
    try:
        sdk = get_sdk()
        # Note: SDK projects API needs to be implemented
        # For now, read from local YAML file
        # TODO: Use sdk.projects.get() when server endpoint exists

        projects_dir = get_projects_dir()
        file_path = projects_dir / f"{project_id}.yaml"

        if not file_path.exists():
            return None

        with open(file_path) as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"Warning: Failed to get project {project_id}: {e}")
        return None


def list_projects(status: str | None = None) -> list[dict[str, Any]]:
    """List projects via API, optionally filtered by status.

    Args:
        status: Filter by status (draft, active, complete, abandoned)

    Returns:
        List of project dictionaries
    """
    try:
        sdk = get_sdk()
        # Note: SDK projects API needs to be implemented
        # For now, read from local YAML files
        # TODO: Use sdk.projects.list() when server endpoint exists

        projects_dir = get_projects_dir()
        if not projects_dir.exists():
            return []

        projects = []
        for file_path in projects_dir.glob("PROJ-*.yaml"):
            with open(file_path) as f:
                project = yaml.safe_load(f)
                if status is None or project.get("status") == status:
                    projects.append(project)

        return projects
    except Exception as e:
        print(f"Warning: Failed to list projects: {e}")
        return []


def activate_project(project_id: str, create_branch: bool = True) -> dict[str, Any] | None:
    """Activate a project and optionally create its feature branch.

    Args:
        project_id: Project identifier
        create_branch: Whether to create the git branch

    Returns:
        Updated project or None if not found
    """
    project = get_project(project_id)
    if not project:
        return None

    # Create git branch if requested
    if create_branch and project.get("branch"):
        base = project.get("base_branch", "main")
        branch = project["branch"]
        try:
            subprocess.run(
                ["git", "checkout", "-b", branch, base],
                capture_output=True,
                check=True,
            )
            # Switch back to base branch
            subprocess.run(["git", "checkout", base], capture_output=True)
        except subprocess.CalledProcessError:
            pass  # Branch may already exist

    # Update status
    project["status"] = "active"
    _write_project_file(project)

    return project


def get_project_tasks(project_id: str) -> list[dict[str, Any]]:
    """Get all tasks belonging to a project.

    Args:
        project_id: Project identifier

    Returns:
        List of task dictionaries
    """
    return []


def get_project_status(project_id: str) -> dict[str, Any] | None:
    """Get detailed project status including task breakdown.

    Args:
        project_id: Project identifier

    Returns:
        Status dictionary or None if project not found
    """
    project = get_project(project_id)
    if not project:
        return None

    tasks = get_project_tasks(project_id)

    # Count tasks by queue
    queue_counts = {}
    for task in tasks:
        queue = task.get("queue", "unknown")
        queue_counts[queue] = queue_counts.get(queue, 0) + 1

    # Check for blocked tasks
    blocked_tasks = [t for t in tasks if t.get("blocked_by")]

    return {
        "project": project,
        "task_count": len(tasks),
        "tasks_by_queue": queue_counts,
        "blocked_count": len(blocked_tasks),
        "tasks": tasks,
    }


def send_to_breakdown(
    title: str,
    description: str,
    context: str,
    created_by: str = "human",
    as_project: bool = True,
) -> dict[str, Any]:
    """Send work to the breakdown queue for decomposition.

    This is the main entry point for async work handoff.

    Args:
        title: Title of the work
        description: Description of what needs to be done
        context: Additional context/background
        created_by: Who created this
        as_project: If True, creates a project; if False, creates a single task

    Returns:
        Dictionary with project_id or task_id and file paths
    """
    if as_project:
        # Create project
        project = create_project(
            title=title,
            description=description,
            created_by=created_by,
        )

        # Create initial breakdown task for the project
        task_path = create_task(
            title=f"Break down: {title}",
            role="breakdown",
            context=f"{description}\n\n{context}",
            acceptance_criteria=[
                "Decompose into right-sized tasks",
                "Create testing strategy task first",
                "Map dependencies between tasks",
                "Each task completable in <30 turns",
            ],
            priority="P1",
            created_by=created_by,
            project_id=project["id"],
            queue="breakdown",
        )

        return {
            "type": "project",
            "project_id": project["id"],
            "project": project,
            "breakdown_task": str(task_path),
        }
    else:
        # Create single task in breakdown queue
        task_path = create_task(
            title=title,
            role="breakdown",
            context=f"{description}\n\n{context}",
            acceptance_criteria=[
                "Break down into smaller tasks if needed",
                "Ensure clear acceptance criteria",
            ],
            priority="P1",
            created_by=created_by,
            queue="breakdown",
        )

        return {
            "type": "task",
            "task_path": str(task_path),
        }


def get_breakdowns_dir() -> Path:
    """Get the breakdowns directory.

    Returns:
        Path to .octopoid/shared/breakdowns/
    """
    breakdowns_dir = get_orchestrator_dir() / "shared" / "breakdowns"
    breakdowns_dir.mkdir(parents=True, exist_ok=True)
    return breakdowns_dir


def list_pending_breakdowns() -> list[dict]:
    """List all pending breakdown files.

    Returns:
        List of breakdown info dicts with path, project_id, title, task_count
    """
    breakdowns_dir = get_breakdowns_dir()
    results = []

    for path in breakdowns_dir.glob("*.md"):
        content = path.read_text()

        # Parse metadata
        project_match = re.search(r'\*\*Project:\*\*\s*(\S+)', content)
        status_match = re.search(r'\*\*Status:\*\*\s*(\S+)', content)
        title_match = re.search(r'^# Breakdown:\s*(.+)$', content, re.MULTILINE)

        # Count tasks
        task_count = len(re.findall(r'^## Task \d+:', content, re.MULTILINE))

        status = status_match.group(1) if status_match else "unknown"

        if status == "pending_review":
            results.append({
                "path": path,
                "project_id": project_match.group(1) if project_match else None,
                "title": title_match.group(1) if title_match else path.stem,
                "task_count": task_count,
                "status": status,
            })

    return results


def approve_breakdown(identifier: str) -> dict[str, Any]:
    """Approve a breakdown and create tasks from it.

    Args:
        identifier: Project ID (PROJ-xxx) or breakdown filename

    Returns:
        Dict with created task info
    """
    breakdowns_dir = get_breakdowns_dir()

    # Find the breakdown file
    if identifier.startswith("PROJ-"):
        breakdown_path = breakdowns_dir / f"{identifier}-breakdown.md"
    else:
        breakdown_path = breakdowns_dir / f"{identifier}.md"
        if not breakdown_path.exists():
            breakdown_path = breakdowns_dir / identifier

    if not breakdown_path.exists():
        raise FileNotFoundError(f"Breakdown file not found: {breakdown_path}")

    content = breakdown_path.read_text()

    # Parse metadata
    project_match = re.search(r'\*\*Project:\*\*\s*(\S+)', content)
    branch_match = re.search(r'\*\*Branch:\*\*\s*(\S+)', content)
    status_match = re.search(r'\*\*Status:\*\*\s*(\S+)', content)
    parent_depth_match = re.search(r'\*\*Parent Breakdown Depth:\*\*\s*(\d+)', content)

    project_id = project_match.group(1) if project_match else None
    branch = branch_match.group(1) if branch_match else "main"
    status = status_match.group(1) if status_match else "unknown"
    parent_breakdown_depth = int(parent_depth_match.group(1)) if parent_depth_match else 0

    if status != "pending_review":
        raise ValueError(f"Breakdown is not pending review (status: {status})")

    # Create feature branch if not main
    if branch and branch != "main":
        _create_and_push_branch(branch)

    # Parse tasks
    tasks = _parse_breakdown_tasks(content)

    if not tasks:
        raise ValueError("No tasks found in breakdown file")

    # Create tasks
    created_ids = []
    id_map = {}  # Map from task number to actual task ID

    for task in tasks:
        task_num = task["number"]
        title = task["title"]
        role = task.get("role", "implement")
        priority = task.get("priority", "P2")
        context = task.get("context", "")
        criteria = task.get("acceptance_criteria", [])
        depends_on = task.get("depends_on", [])

        # Resolve dependencies to actual task IDs
        blocked_by = None
        if depends_on:
            blocker_ids = []
            for dep_num in depends_on:
                if dep_num in id_map:
                    blocker_ids.append(id_map[dep_num])
            if blocker_ids:
                blocked_by = ",".join(blocker_ids)

        task_path = create_task(
            title=title,
            role=role,
            context=context,
            acceptance_criteria=criteria if criteria else ["Complete the task"],
            priority=priority,
            branch=branch,
            created_by="human",
            blocked_by=blocked_by,
            project_id=project_id,
            queue="incoming",
            breakdown_depth=parent_breakdown_depth + 1,
        )

        # Extract task ID from path
        task_id = task_path.stem.replace("TASK-", "")
        id_map[task_num] = task_id
        created_ids.append(task_id)

    # Find leaf subtasks (ones that no other subtask depends on)
    # These are the "exit points" of the breakdown
    depended_on = set()
    for task in tasks:
        for dep_num in task.get("depends_on", []):
            if dep_num in id_map:
                depended_on.add(id_map[dep_num])
    leaf_ids = [tid for tid in created_ids if tid not in depended_on]

    # Update breakdown file status to approved
    updated_content = content.replace(
        "**Status:** pending_review",
        f"**Status:** approved\n**Approved:** {datetime.now().isoformat()}"
    )
    breakdown_path.write_text(updated_content)

    return {
        "breakdown_file": str(breakdown_path),
        "project_id": project_id,
        "tasks_created": len(created_ids),
        "task_ids": created_ids,
        "leaf_ids": leaf_ids,
    }


def _create_and_push_branch(branch: str, base_branch: str = "main") -> bool:
    """Create a feature branch and push it to origin.

    Called during breakdown approval to ensure the branch exists
    before agents try to check it out.

    Args:
        branch: Name of the feature branch to create
        base_branch: Branch to create from (default: main)

    Returns:
        True if branch was created or already exists, False on error
    """
    try:
        # First, fetch to make sure we have latest
        subprocess.run(
            ["git", "fetch", "origin"],
            capture_output=True,
            check=False,
        )

        # Check if branch already exists on origin
        result = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", branch],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            # Branch already exists on origin
            return True

        # Check if branch exists locally
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            capture_output=True,
            text=True,
        )
        branch_exists_locally = result.returncode == 0

        if not branch_exists_locally:
            # Create the branch from base_branch
            subprocess.run(
                ["git", "branch", branch, f"origin/{base_branch}"],
                capture_output=True,
                check=True,
            )

        # Push to origin
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            capture_output=True,
            check=True,
        )

        return True

    except subprocess.CalledProcessError as e:
        # Log but don't fail - agents will report the error if branch is missing
        import sys
        print(f"Warning: Failed to create/push branch {branch}: {e}", file=sys.stderr)
        return False


def _parse_breakdown_tasks(content: str) -> list[dict]:
    """Parse tasks from a breakdown markdown file.

    Args:
        content: Markdown content of breakdown file

    Returns:
        List of task dicts with number, title, role, priority, context, criteria, depends_on
    """
    tasks = []

    # Split on task headers
    task_pattern = r'^## Task (\d+):\s*(.+)$'
    task_matches = list(re.finditer(task_pattern, content, re.MULTILINE))

    for i, match in enumerate(task_matches):
        task_num = int(match.group(1))
        title = match.group(2).strip()

        # Get content until next task or end
        start = match.end()
        end = task_matches[i + 1].start() if i + 1 < len(task_matches) else len(content)
        task_content = content[start:end]

        # Parse metadata
        role_match = re.search(r'\*\*Role:\*\*\s*(\S+)', task_content)
        priority_match = re.search(r'\*\*Priority:\*\*\s*(\S+)', task_content)
        depends_match = re.search(r'\*\*Depends on:\*\*\s*(.+)', task_content)

        role = role_match.group(1) if role_match else "implement"
        priority = priority_match.group(1) if priority_match else "P2"

        # Parse depends_on
        depends_on = []
        if depends_match:
            deps_str = depends_match.group(1).strip()
            if deps_str != "(none)":
                # Parse comma-separated numbers
                for dep in deps_str.split(","):
                    dep = dep.strip()
                    if dep.isdigit():
                        depends_on.append(int(dep))

        # Parse context
        context = ""
        context_match = re.search(r'### Context\s*\n(.+?)(?=\n###|\n---|\Z)', task_content, re.DOTALL)
        if context_match:
            context = context_match.group(1).strip()

        # Parse acceptance criteria
        criteria = []
        criteria_match = re.search(r'### Acceptance Criteria\s*\n(.+?)(?=\n###|\n---|\Z)', task_content, re.DOTALL)
        if criteria_match:
            criteria_text = criteria_match.group(1)
            for line in criteria_text.strip().split("\n"):
                # Match both checked and unchecked items
                item_match = re.match(r'^-\s*\[[ x]\]\s*(.+)$', line.strip())
                if item_match:
                    criteria.append(item_match.group(1))

        # Parse verification (user code path test requirement)
        verification = ""
        verification_match = re.search(
            r'### Verification \(User Code Path\)\s*\n(.+?)(?=\n###|\n---|\Z)',
            task_content, re.DOTALL,
        )
        if verification_match:
            verification = verification_match.group(1).strip()
            # Strip leading > from blockquote
            verification = re.sub(r'^>\s*', '', verification)

        # Append verification to context so agents see it prominently
        if verification:
            context += f"\n\n## VERIFICATION REQUIREMENT\n\nYou MUST write a test that exercises the user's actual code path:\n\n{verification}\n\nThis test must call the real user-facing function, NOT a helper. If this test passes, the feature works. If you can't make this test pass, the task is not done."

        tasks.append({
            "number": task_num,
            "title": title,
            "role": role,
            "priority": priority,
            "context": context,
            "acceptance_criteria": criteria,
            "depends_on": depends_on,
        })

    return tasks


# =============================================================================
# Agent Notes — persist learnings across attempts
# =============================================================================

# Max chars of stdout to save per attempt
NOTES_STDOUT_LIMIT = 3000


def get_task_notes(task_id: str) -> str | None:
    """Read accumulated agent notes for a task.

    Args:
        task_id: Task identifier (short hash)

    Returns:
        Notes content string, or None if no notes exist
    """
    from .config import get_notes_dir
    notes_path = get_notes_dir() / f"TASK-{task_id}.md"
    if notes_path.exists():
        try:
            return notes_path.read_text()
        except IOError:
            return None
    return None


def save_task_notes(
    task_id: str,
    agent_name: str,
    stdout: str,
    commits: int = 0,
    turns: int = 0,
) -> None:
    """Append a run summary to the notes file for a task.

    Each call adds a new attempt section with metadata and a tail
    of stdout (last NOTES_STDOUT_LIMIT chars).

    Args:
        task_id: Task identifier
        agent_name: Name of the agent that ran
        stdout: Full stdout from Claude invocation
        commits: Commits made this attempt
        turns: Turns used this attempt
    """
    from .config import get_notes_dir
    notes_dir = get_notes_dir()
    notes_dir.mkdir(parents=True, exist_ok=True)
    notes_path = notes_dir / f"TASK-{task_id}.md"

    # Count existing attempts
    attempt = 1
    if notes_path.exists():
        try:
            existing = notes_path.read_text()
            attempt = existing.count("## Attempt ") + 1
        except IOError:
            pass

    # Truncate stdout to tail
    stdout_tail = stdout[-NOTES_STDOUT_LIMIT:] if len(stdout) > NOTES_STDOUT_LIMIT else stdout
    if len(stdout) > NOTES_STDOUT_LIMIT:
        stdout_tail = f"[...truncated {len(stdout) - NOTES_STDOUT_LIMIT} chars...]\n" + stdout_tail

    timestamp = datetime.now().isoformat()
    section = f"""
## Attempt {attempt} — {agent_name} ({timestamp})
Turns: {turns} | Commits: {commits}

{stdout_tail.strip()}

"""

    with open(notes_path, "a") as f:
        # Write header on first attempt
        if attempt == 1:
            f.write(f"# Agent Notes: TASK-{task_id}\n")
        f.write(section)


def cleanup_task_notes(task_id: str) -> bool:
    """Delete notes file for a completed task.

    Args:
        task_id: Task identifier

    Returns:
        True if notes file existed and was deleted
    """
    from .config import get_notes_dir
    notes_path = get_notes_dir() / f"TASK-{task_id}.md"
    if notes_path.exists():
        try:
            notes_path.unlink()
            return True
        except IOError:
            return False
    return False


# =============================================================================
# Task Recycling
# =============================================================================


BURNED_OUT_TURN_THRESHOLD = 80


def is_burned_out(commits_count: int, turns_used: int) -> bool:
    """Check if a task is burned out (used many turns without producing commits).

    DISABLED: This check is currently disabled due to persistent false positives
    from commit counting bugs. The commit counting system has issues with
    persistent worktrees + branch switching during agent work, causing tasks
    with real commits to be incorrectly detected as burned out and recycled.

    Multiple false positives observed in 2026-02 (tasks e11a484b, f7b4d710,
    58e22e70) where tasks reported 0 commits but actually had commits.

    This check can be re-enabled after ephemeral worktrees are implemented
    (TASK-f7b4d710), which will resolve the underlying commit counting issues.

    Args:
        commits_count: Number of commits the agent made
        turns_used: Number of turns the agent used

    Returns:
        Always False (check disabled)
    """
    # Disabled - return False unconditionally to prevent false positive recycling
    return False


def recycle_to_breakdown(task_path, reason="too_large") -> dict | None:
    """Recycle a failed/burned-out task back to the breakdown queue.

    Builds rich context from the project state (completed siblings, branch info)
    and creates a new breakdown task. The original task is moved to a 'recycled'
    state and any tasks blocked by it are rewired to depend on the new breakdown task.

    Args:
        task_path: Path to the burned-out task file
        reason: Why the task is being recycled

    Returns:
        Dictionary with breakdown_task info, or None if recycling is not appropriate
        (e.g., depth cap exceeded)
    """
    task_path = Path(task_path)

    # Look up task via SDK
    match = re.match(r"TASK-(.+)\.md", task_path.name)
    if not match:
        return None
    task_id = match.group(1)

    try:
        sdk = get_sdk()
        db_task = sdk.tasks.get(task_id)
    except Exception:
        db_task = None

    if not db_task:
        return None

    # Read the original task content, stripping agent metadata and error
    # sections to keep re-breakdown tasks lean (only human-written content)
    task_content = task_path.read_text() if task_path.exists() else ""
    for marker in ["CLAIMED_BY:", "CLAIMED_AT:", "SUBMITTED_AT:", "COMMITS_COUNT:", "TURNS_USED:", "FAILED_AT:", "## Error"]:
        idx = task_content.find(marker)
        if idx > 0:
            task_content = task_content[:idx].rstrip()
            break

    # Check depth cap - don't recycle tasks that are already re-breakdowns
    depth_match = re.search(r"RE_BREAKDOWN_DEPTH:\s*(\d+)", task_content)
    current_depth = int(depth_match.group(1)) if depth_match else 0
    if current_depth >= 1:
        return None  # Escalate to human instead

    # Gather project context
    project_id = db_task.get("project_id")
    project = None
    sibling_tasks = []
    project_context = ""

    if project_id:
        project = get_project(project_id)
        all_project_tasks = get_project_tasks(project_id)
        sibling_tasks = [t for t in all_project_tasks if t["id"] != task_id]

        if project:
            project_context = (
                f"## Project Context\n\n"
                f"**Project:** {project_id}\n"
                f"**Title:** {project.get('title', 'Unknown')}\n"
                f"**Branch:** {project.get('branch', 'main')}\n\n"
            )

        # Build completed siblings summary
        done_tasks = [t for t in sibling_tasks if t.get("queue") == "done"]
        if done_tasks:
            project_context += "### Completed Tasks\n\n"
            for t in done_tasks:
                commits = t.get("commits_count", 0)
                commit_label = f"{commits} commit{'s' if commits != 1 else ''}"
                project_context += f"- **{t['id']}** ({commit_label})\n"
            project_context += "\n"

    # Build the breakdown task content
    new_depth = current_depth + 1
    breakdown_context = (
        f"{project_context}"
        f"## Recycled Task\n\n"
        f"The following task burned out (0 commits after max turns) and needs "
        f"to be re-broken-down into smaller subtasks.\n\n"
        f"### Original Task: {task_id}\n\n"
        f"```\n{task_content}\n```\n\n"
        f"## Instructions\n\n"
        f"1. Check out the project branch and examine the current state of the code\n"
        f"2. Identify what work from the original task has NOT been completed\n"
        f"3. Break the remaining work into smaller, focused subtasks\n"
        f"4. Each subtask should be completable in <30 agent turns\n"
    )

    # Create the breakdown task
    breakdown_task_path = create_task(
        title=f"Re-breakdown: {task_id}",
        role="breakdown",
        context=breakdown_context,
        acceptance_criteria=[
            "Examine branch state to identify completed vs remaining work",
            "Decompose remaining work into right-sized tasks (<30 turns each)",
            "Map dependencies between new subtasks",
            "Include RE_BREAKDOWN_DEPTH in new subtasks",
        ],
        priority="P1",
        branch=project.get("branch", "main") if project else db_task.get("branch", "main"),
        created_by="recycler",
        project_id=project_id,
        queue="breakdown",
    )

    # Extract the new breakdown task ID from filename
    breakdown_match = re.match(r"TASK-(.+)\.md", breakdown_task_path.name)
    breakdown_task_id = breakdown_match.group(1) if breakdown_match else None

    # Add RE_BREAKDOWN_DEPTH to the breakdown task file
    if breakdown_task_path.exists():
        content = breakdown_task_path.read_text()
        # Insert after CREATED_BY line
        content = content.replace(
            "CREATED_BY: recycler\n",
            f"CREATED_BY: recycler\nRE_BREAKDOWN_DEPTH: {new_depth}\n",
        )
        breakdown_task_path.write_text(content)

    # Move original task to recycled state
    recycled_dir = get_queue_subdir("recycled")
    recycled_dir.mkdir(parents=True, exist_ok=True)
    recycled_path = recycled_dir / task_path.name

    if task_path.exists():
        task_path.rename(recycled_path)

    # NOTE: We intentionally do NOT rewire dependencies here.
    # External tasks stay blocked by the original (recycled) task ID.
    # When the breakdown is approved, approve_breakdown() rewires from
    # the original task to the leaf subtasks. This avoids a race where
    # the breakdown task gets accepted → _unblock_dependent_tasks fires
    # → external tasks unblock before the real work is done.

    return {
        "breakdown_task": str(breakdown_task_path),
        "breakdown_task_id": breakdown_task_id,
        "original_task_id": task_id,
        "action": "recycled",
    }




def approve_and_merge(
    task_id: str,
    merge_method: str = "merge",
) -> dict[str, Any]:
    """Approve a task and merge its PR via BEFORE_MERGE hooks.

    Runs configured BEFORE_MERGE hooks (default: merge_pr) then accepts
    the task via the SDK.  If hooks fail, the task is NOT accepted.

    Args:
        task_id: Task identifier
        merge_method: Git merge method (merge, squash, rebase)

    Returns:
        Dict with result info (merged, pr_url, error)
    """
    from .hooks import HookContext, HookPoint, HookStatus, run_hooks

    sdk = get_sdk()
    task = sdk.tasks.get(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}

    pr_number = task.get("pr_number")
    pr_url = task.get("pr_url")

    result: dict[str, Any] = {"task_id": task_id, "merged": False, "pr_url": pr_url}

    # Build hook context
    ctx = HookContext(
        task_id=task_id,
        task_title=task.get("title", ""),
        task_path=task.get("file_path", ""),
        task_type=task.get("type"),
        branch_name=task.get("branch_name", ""),
        base_branch=task.get("base_branch", "main"),
        worktree=Path(task.get("file_path", "")).parent,
        agent_name=task.get("assigned_to", ""),
        extra={
            "pr_number": pr_number,
            "pr_url": pr_url,
            "merge_method": merge_method,
        },
    )

    # Run BEFORE_MERGE hooks (e.g. merge_pr)
    all_ok, hook_results = run_hooks(HookPoint.BEFORE_MERGE, ctx)

    if not all_ok:
        last = hook_results[-1] if hook_results else None
        error_msg = last.message if last else "BEFORE_MERGE hooks failed"
        result["error"] = error_msg
        return result

    # Check hook results for merge info
    for hr in hook_results:
        if hr.status == HookStatus.SUCCESS and hr.context.get("pr_number"):
            result["merged"] = True
            break

    # Accept the task via SDK
    sdk.tasks.accept(task_id, accepted_by="scheduler")

    cleanup_task_notes(task_id)

    return result


