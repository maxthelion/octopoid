"""Review local submodule commits for an orchestrator specialist task.

Shows the commits an agent has made to the orchestrator submodule's
main branch inside their worktree that haven't been pushed to
origin/main yet.  This is the read-only counterpart to
approve_orch.py — it lets a reviewer inspect what an agent did before
running the approval flow.

Usage:
    .octopoid/venv/bin/python orchestrator/scripts/review-orchestrator-task <task-id-prefix>
"""

import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import get_agents_runtime_dir


SUBMODULE_BRANCH = "main"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(
    cmd: list[str],
    cwd: Path | str | None = None,
    check: bool = False,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Step 0 — Resolve task
# ---------------------------------------------------------------------------


def resolve_task_id(prefix: str) -> dict[str, Any] | None:
    """Resolve a task ID prefix to a full task record.

    Returns the task dict or None (prints diagnostic).
    Uses the API via OctopoidSDK.
    """
    from octopoid_sdk import OctopoidSDK

    sdk = OctopoidSDK()
    try:
        tasks = sdk.list_tasks()
    except Exception as exc:
        print(f"ERROR: Could not query API: {exc}")
        return None

    matches = [t for t in tasks if t.get("id", "").startswith(prefix)]

    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        print(f"ERROR: Ambiguous prefix '{prefix}' matches {len(matches)} tasks:")
        for t in matches:
            print(f"  {t['id']}")
        return None
    else:
        print(f"ERROR: No task found for prefix '{prefix}'")
        return None


# ---------------------------------------------------------------------------
# Step 1 — Locate agent worktree submodule
# ---------------------------------------------------------------------------


def find_agent_submodule(task_info: dict[str, Any]) -> Path | None:
    """Find the orchestrator submodule inside the agent's worktree.

    Looks up claimed_by to find the agent name, then checks
    .octopoid/agents/<name>/worktree/orchestrator/.

    Returns the absolute path to the submodule directory, or None.
    """
    agent_name = task_info.get("claimed_by")

    if not agent_name:
        print("ERROR: Cannot determine agent name (claimed_by is empty and no claim history)")
        return None

    worktree_sub = get_agents_runtime_dir() / agent_name / "worktree" / "orchestrator"
    if not worktree_sub.exists():
        print(f"ERROR: Agent worktree submodule not found at {worktree_sub}")
        return None

    return worktree_sub


# ---------------------------------------------------------------------------
# Step 2 — Check submodule branch
# ---------------------------------------------------------------------------


def check_submodule_branch(agent_sub: Path) -> str | None:
    """Verify the agent's submodule is on main.

    Returns the branch name if valid, or None with an error message.
    """
    result = _run(["git", "branch", "--show-current"], cwd=agent_sub)
    if result.returncode != 0:
        print(f"ERROR: Could not determine branch in {agent_sub}")
        print(f"  stderr: {result.stderr.strip()}")
        return None

    branch = result.stdout.strip()
    if not branch:
        print(f"ERROR: Agent submodule is in detached HEAD state at {agent_sub}")
        return None

    if branch != SUBMODULE_BRANCH:
        print(f"ERROR: Agent submodule is on branch '{branch}', expected '{SUBMODULE_BRANCH}'")
        return None

    return branch


# ---------------------------------------------------------------------------
# Step 3 — Find local commits not on origin
# ---------------------------------------------------------------------------


def get_local_commits(agent_sub: Path) -> list[dict[str, str]]:
    """Get commits on main that are not on origin/main.

    Returns a list of dicts with 'sha', 'subject', 'author', 'date' keys,
    oldest first. Returns empty list if there are no unpushed commits.
    """
    # Fetch origin to make sure we have the latest ref
    _run(
        ["git", "fetch", "origin", SUBMODULE_BRANCH],
        cwd=agent_sub,
    )

    # Check if origin/main exists
    ref_check = _run(
        ["git", "rev-parse", "--verify", f"origin/{SUBMODULE_BRANCH}"],
        cwd=agent_sub,
    )
    if ref_check.returncode != 0:
        print(f"WARNING: origin/{SUBMODULE_BRANCH} not found, showing all commits on {SUBMODULE_BRANCH}")
        # Show all commits on the branch
        result = _run(
            ["git", "log", "--format=%H|%s|%an|%ai", "--reverse", SUBMODULE_BRANCH],
            cwd=agent_sub,
        )
    else:
        result = _run(
            ["git", "log", "--format=%H|%s|%an|%ai", "--reverse",
             f"origin/{SUBMODULE_BRANCH}..{SUBMODULE_BRANCH}"],
            cwd=agent_sub,
        )

    if result.returncode != 0 or not result.stdout.strip():
        return []

    commits = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|", 3)
        if len(parts) >= 2:
            commits.append({
                "sha": parts[0],
                "subject": parts[1],
                "author": parts[2] if len(parts) > 2 else "",
                "date": parts[3] if len(parts) > 3 else "",
            })

    return commits


# ---------------------------------------------------------------------------
# Step 4 — Get diff
# ---------------------------------------------------------------------------


def get_diff(agent_sub: Path) -> str:
    """Get the diff of local commits vs origin/main.

    Returns the diff text, or empty string if no diff.
    """
    ref_check = _run(
        ["git", "rev-parse", "--verify", f"origin/{SUBMODULE_BRANCH}"],
        cwd=agent_sub,
    )

    if ref_check.returncode != 0:
        # No origin ref, show diff of all commits
        result = _run(
            ["git", "diff", "--stat", "HEAD~1..HEAD"],
            cwd=agent_sub,
        )
    else:
        result = _run(
            ["git", "diff", f"origin/{SUBMODULE_BRANCH}..{SUBMODULE_BRANCH}"],
            cwd=agent_sub,
        )

    if result.returncode != 0:
        return ""

    return result.stdout


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def review_orchestrator_task(task_id_prefix: str) -> int:
    """Run the review flow. Returns 0 on success, non-zero on failure."""

    # Strip TASK- prefix
    prefix = task_id_prefix
    if prefix.startswith("TASK-"):
        prefix = prefix[5:]

    # Step 0: Resolve task
    task_info = resolve_task_id(prefix)
    if not task_info:
        return 1

    task_id = task_info["id"]
    role = task_info.get("role", "")
    queue = task_info.get("queue", "")
    claimed_by = task_info.get("claimed_by", "")

    print(f"Task:      TASK-{task_id}")
    print(f"Role:      {role}")
    print(f"Queue:     {queue}")
    print(f"Claimed:   {claimed_by or '(none)'}")

    if role != "orchestrator_impl":
        print(f"\nERROR: Task has role='{role}', not 'orchestrator_impl'")
        print("This script is only for orchestrator specialist tasks.")
        return 1

    # Step 1: Find agent worktree submodule
    print(f"\nLocating agent worktree...")
    agent_sub = find_agent_submodule(task_info)
    if not agent_sub:
        return 1
    print(f"Submodule: {agent_sub}")

    # Step 2: Check branch
    branch = check_submodule_branch(agent_sub)
    if not branch:
        return 1
    print(f"Branch:    {branch}")

    # Step 3: Get local commits
    print(f"\n{'=' * 60}")
    print(f"LOCAL COMMITS (not on origin/{SUBMODULE_BRANCH})")
    print(f"{'=' * 60}")

    commits = get_local_commits(agent_sub)

    if not commits:
        print("\n(no local commits found)")
        print(f"\nThe agent has not made any commits to {SUBMODULE_BRANCH}")
        print(f"that differ from origin/{SUBMODULE_BRANCH}.")
        return 0

    print()
    for c in commits:
        sha_short = c["sha"][:8]
        print(f"  {sha_short}  {c['subject']}")
        if c.get("author"):
            print(f"           by {c['author']} on {c.get('date', '')}")

    print(f"\n  {len(commits)} commit(s) total")

    # Step 4: Show diff
    print(f"\n{'=' * 60}")
    print(f"DIFF (origin/{SUBMODULE_BRANCH}..{SUBMODULE_BRANCH})")
    print(f"{'=' * 60}\n")

    diff = get_diff(agent_sub)
    if diff:
        print(diff)
    else:
        print("(no diff)")

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: review-orchestrator-task <task-id>")
        print()
        print("Shows the local submodule commits for an orchestrator_impl task.")
        print("Accepts full or partial task IDs, with or without TASK- prefix.")
        print()
        print("Examples:")
        print("  review-orchestrator-task f2da3c14")
        print("  review-orchestrator-task TASK-f2da3c14")
        sys.exit(1)

    sys.exit(review_orchestrator_task(sys.argv[1]))


if __name__ == "__main__":
    main()
