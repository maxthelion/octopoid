"""Full approval automation for orchestrator specialist tasks.

Orchestrator specialist agents (role=orchestrator_impl) commit to the
orchestrator submodule's main branch inside their worktree.
Approving a task means landing those commits on the canonical main
branch, running tests, pushing, updating the submodule ref on main, and
accepting in the DB.

Usage:
    .orchestrator/venv/bin/python -m orchestrator.approve_orch <task-id-prefix>
"""

import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import find_parent_project, get_agents_runtime_dir, is_db_enabled
from .db import accept_completion, get_connection, get_task, update_task_queue


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUBMODULE_BRANCH = "main"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    return find_parent_project()


def _submodule_dir() -> Path:
    return _repo_root() / "orchestrator"


def run(
    cmd: list[str],
    cwd: Path | str | None = None,
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or _repo_root(),
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    return result


# ---------------------------------------------------------------------------
# Step 0 — Resolve task
# ---------------------------------------------------------------------------


def resolve_task_id(prefix: str) -> dict[str, Any] | None:
    """Resolve a task ID prefix to a full task record.

    Returns the task dict or None (prints diagnostic).
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, role, queue, claimed_by FROM tasks WHERE id LIKE ?",
            (f"{prefix}%",),
        ).fetchall()

    if len(rows) == 1:
        return dict(rows[0])
    elif len(rows) > 1:
        print(f"Ambiguous prefix '{prefix}' matches {len(rows)} tasks:")
        for r in rows:
            print(f"  {r['id']}")
        return None
    else:
        print(f"No task found for prefix '{prefix}'")
        return None


# ---------------------------------------------------------------------------
# Step 1 — Locate agent worktree and its submodule
# ---------------------------------------------------------------------------


def find_agent_submodule(task_info: dict[str, Any]) -> Path | None:
    """Find the orchestrator submodule inside the agent's worktree.

    The agent name comes from task.claimed_by (still set for provisional
    tasks). The worktree lives at .orchestrator/agents/<name>/worktree/
    and its orchestrator submodule is in the ``orchestrator/`` sub-dir.

    Returns the absolute path to the submodule directory, or None.
    """
    agent_name = task_info.get("claimed_by")
    if not agent_name:
        # Try to find it from history
        task_id = task_info["id"]
        with get_connection() as conn:
            row = conn.execute(
                "SELECT agent FROM task_history "
                "WHERE task_id = ? AND event = 'claimed' "
                "ORDER BY timestamp DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if row:
            agent_name = row["agent"]

    if not agent_name:
        print("ERROR: Cannot determine agent name (claimed_by is empty and no claim history)")
        return None

    worktree_sub = get_agents_runtime_dir() / agent_name / "worktree" / "orchestrator"
    if not worktree_sub.exists():
        print(f"ERROR: Agent worktree submodule not found at {worktree_sub}")
        return None

    return worktree_sub


# ---------------------------------------------------------------------------
# Step 2 — Find agent commits
# ---------------------------------------------------------------------------


def find_agent_commits(agent_sub: Path, local_sub: Path) -> list[str]:
    """Return commit SHAs from agent submodule that are not in local_sub.

    We detect the agent's current branch (which may be a feature branch
    like orch/<task-id> or main), fetch it, then compare FETCH_HEAD
    against the local main HEAD.  Returns a list of SHAs in
    topological order (oldest first), ready for cherry-pick.
    """
    # Get local HEAD
    local_head = run(
        ["git", "rev-parse", "HEAD"], cwd=local_sub, check=False
    )
    if local_head.returncode != 0:
        return []
    local_sha = local_head.stdout.strip()

    # Detect the agent's current branch (could be orch/<task-id> or main)
    agent_branch_result = run(
        ["git", "branch", "--show-current"], cwd=agent_sub, check=False
    )
    agent_branch = agent_branch_result.stdout.strip() if agent_branch_result.returncode == 0 else ""
    if not agent_branch:
        agent_branch = "HEAD"  # Detached HEAD fallback
    print(f"  Agent submodule branch: {agent_branch}")

    # Fetch from agent submodule so we can compare
    fetch_result = run(
        ["git", "fetch", str(agent_sub), agent_branch],
        cwd=local_sub,
        check=False,
    )
    if fetch_result.returncode != 0:
        print(f"  WARNING: fetch from agent submodule failed: {fetch_result.stderr.strip()}")
        # Fallback: try fetching main
        if agent_branch != SUBMODULE_BRANCH:
            fetch_result = run(
                ["git", "fetch", str(agent_sub), SUBMODULE_BRANCH],
                cwd=local_sub,
                check=False,
            )
            if fetch_result.returncode != 0:
                return []
        else:
            return []

    # Get FETCH_HEAD
    fetch_head = run(
        ["git", "rev-parse", "FETCH_HEAD"], cwd=local_sub, check=False
    )
    if fetch_head.returncode != 0:
        return []
    agent_sha = fetch_head.stdout.strip()

    if agent_sha == local_sha:
        return []  # Identical — nothing to do

    # Find commits reachable from FETCH_HEAD but not from local HEAD.
    result = run(
        ["git", "rev-list", "--reverse", f"{local_sha}..FETCH_HEAD"],
        cwd=local_sub,
        check=False,
    )

    if result.returncode != 0 or not result.stdout.strip():
        # Might be totally divergent — try via merge-base
        merge_base = run(
            ["git", "merge-base", local_sha, "FETCH_HEAD"],
            cwd=local_sub,
            check=False,
        )
        if merge_base.returncode != 0:
            # No common ancestor; list all FETCH_HEAD commits
            result = run(
                ["git", "rev-list", "--reverse", "FETCH_HEAD"],
                cwd=local_sub,
                check=False,
            )
        else:
            base = merge_base.stdout.strip()
            result = run(
                ["git", "rev-list", "--reverse", f"{base}..FETCH_HEAD"],
                cwd=local_sub,
                check=False,
            )

    if result.returncode != 0 or not result.stdout.strip():
        return []

    return result.stdout.strip().splitlines()


# ---------------------------------------------------------------------------
# Step 3 — Cherry-pick
# ---------------------------------------------------------------------------


def _is_empty_cherry_pick(result: subprocess.CompletedProcess) -> bool:
    """Detect whether a cherry-pick failed because it produced an empty commit.

    This happens when the patch has already been applied (e.g., re-running
    the approval script after a partial failure).  Git reports messages
    containing 'nothing to commit', 'empty', or 'allow-empty' in this case.
    """
    combined = (result.stdout + result.stderr).lower()
    return any(
        phrase in combined
        for phrase in [
            "nothing to commit",
            "empty",
            "allow-empty",
            "previously applied",
        ]
    )


def cherry_pick_commits(commits: list[str], local_sub: Path) -> bool:
    """Cherry-pick commits one by one onto the current main.

    Returns True on success, False on conflict.  On conflict the cherry-pick
    is aborted so the working tree is clean.

    Already-applied commits (empty cherry-picks) are detected and skipped,
    making this function safe to re-run after a partial failure.
    """
    skipped = 0
    for sha in commits:
        # Get commit message for display
        msg_result = run(
            ["git", "log", "--format=%s", "-1", sha],
            cwd=local_sub,
            check=False,
        )
        msg = msg_result.stdout.strip() if msg_result.returncode == 0 else sha[:8]
        print(f"  Cherry-picking {sha[:8]} ({msg}) ...")

        result = run(
            ["git", "cherry-pick", sha],
            cwd=local_sub,
            check=False,
        )
        if result.returncode != 0:
            # Check if this is an empty cherry-pick (already applied)
            if _is_empty_cherry_pick(result):
                print(f"  Skipping {sha[:8]} — already applied")
                # Reset any cherry-pick state
                run(["git", "cherry-pick", "--abort"], cwd=local_sub, check=False)
                skipped += 1
                continue

            print(f"\n  CONFLICT during cherry-pick of {sha[:8]}")
            print(f"  stderr: {result.stderr.strip()}")

            # Show conflicted files
            status = run(["git", "status", "--short"], cwd=local_sub, check=False)
            if status.stdout.strip():
                print(f"\n  Conflicted files:\n{status.stdout}")

            # Abort the cherry-pick
            run(["git", "cherry-pick", "--abort"], cwd=local_sub, check=False)

            print("\n  Cherry-pick aborted. To resolve manually:")
            print(f"    cd {local_sub}")
            print(f"    git cherry-pick {sha}")
            print("    # resolve conflicts, then git cherry-pick --continue")
            return False

    if skipped:
        print(f"  ({skipped} commit(s) already applied, skipped)")
    return True


# ---------------------------------------------------------------------------
# Step 4 — Run tests
# ---------------------------------------------------------------------------


def run_tests(local_sub: Path) -> bool:
    """Run pytest in the orchestrator submodule.

    Returns True if tests pass, False otherwise.
    """
    # Find venv python
    venv_python = local_sub / "venv" / "bin" / "python"
    if not venv_python.exists():
        # Try the .orchestrator venv
        venv_python = _repo_root() / ".orchestrator" / "venv" / "bin" / "python"

    if not venv_python.exists():
        print("  WARNING: No venv found, skipping tests")
        return True

    print("  Running tests...")
    result = run(
        [str(venv_python), "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=local_sub,
        check=False,
        timeout=300,
    )

    if result.returncode != 0:
        print(f"\n  Tests FAILED (exit code {result.returncode})")
        # Show last 30 lines of output
        lines = result.stdout.strip().splitlines()
        tail = lines[-30:] if len(lines) > 30 else lines
        print("\n  " + "\n  ".join(tail))
        return False

    # Count passed
    for line in result.stdout.splitlines():
        if "passed" in line:
            print(f"  {line.strip()}")
            break

    return True


# ---------------------------------------------------------------------------
# Step 5 — Push main
# ---------------------------------------------------------------------------


def push_submodule(local_sub: Path) -> bool:
    """Push main to origin, handling remote divergence.

    Returns True on success.
    """
    # Fetch first to detect divergence
    run(["git", "fetch", "origin", SUBMODULE_BRANCH], cwd=local_sub, check=False)

    result = run(
        ["git", "push", "origin", SUBMODULE_BRANCH],
        cwd=local_sub,
        check=False,
    )
    if result.returncode == 0:
        return True

    if "Everything up-to-date" in result.stderr:
        return True

    # Push failed — might be non-fast-forward
    if "non-fast-forward" in result.stderr or "fetch first" in result.stderr:
        print("  Remote has diverged. Rebasing onto latest origin...")
        rebase = run(
            ["git", "pull", "--rebase", "origin", SUBMODULE_BRANCH],
            cwd=local_sub,
            check=False,
        )
        if rebase.returncode != 0:
            print(f"  Rebase failed: {rebase.stderr.strip()}")
            run(["git", "rebase", "--abort"], cwd=local_sub, check=False)
            return False

        # Retry push
        result = run(
            ["git", "push", "origin", SUBMODULE_BRANCH],
            cwd=local_sub,
            check=False,
        )
        if result.returncode != 0:
            print(f"  Push still failed: {result.stderr.strip()}")
            return False

    else:
        print(f"  Push failed: {result.stderr.strip()}")
        return False

    return True


# ---------------------------------------------------------------------------
# Step 6 — Update submodule ref on main
# ---------------------------------------------------------------------------


def update_submodule_ref(task_id: str) -> bool:
    """Stage the submodule pointer change, commit, and push main.

    Returns True on success.
    """
    repo = _repo_root()

    # Check we're on main
    branch = run(["git", "branch", "--show-current"], cwd=repo).stdout.strip()
    if branch != "main":
        print(f"  ERROR: Main repo must be on 'main' (currently on '{branch}')")
        return False

    run(["git", "add", "orchestrator"], cwd=repo)

    # Check if there's actually a diff
    diff = run(["git", "diff", "--cached", "--quiet"], cwd=repo, check=False)
    if diff.returncode == 0:
        print("  Submodule ref already up to date")
        return True

    # Read the task file to extract the title
    task = get_task(task_id)
    title = task_id[:8]
    if task and task.get("file_path"):
        try:
            import re
            content = Path(task["file_path"]).read_text()
            title_match = re.search(r"^#\s*\[TASK-[^\]]+\]\s*(.+)$", content, re.MULTILINE)
            if title_match:
                title = title_match.group(1).strip()
        except (IOError, OSError):
            pass

    msg = f"chore: update orchestrator submodule ({title})"
    run(["git", "commit", "-m", msg], cwd=repo)
    print(f"  Committed: {msg}")

    # Push main
    result = run(["git", "push", "origin", "main"], cwd=repo, check=False)
    if result.returncode == 0:
        return True

    if "non-fast-forward" in result.stderr or "fetch first" in result.stderr:
        print("  Main has diverged, pulling and retrying...")
        run(["git", "pull", "--rebase", "origin", "main"], cwd=repo)
        result = run(["git", "push", "origin", "main"], cwd=repo, check=False)
        if result.returncode == 0:
            return True
        print(f"  Push still failed: {result.stderr.strip()}")
        return False

    print(f"  Push failed: {result.stderr.strip()}")
    return False


# ---------------------------------------------------------------------------
# Step 7 — Accept in DB
# ---------------------------------------------------------------------------


def accept_in_db(task_id: str) -> bool:
    """Move task to done, clear claimed_by, unblock dependents.

    Idempotent — safe to call multiple times.  If the task is already
    in the 'done' queue, just ensures claimed_by is cleared.

    Uses update_task_queue() to guarantee side effects (unblocking
    dependents, clearing claimed_by) are always applied.
    """
    # Check if already done to avoid duplicate history entries
    task = get_task(task_id)
    if task and task.get("queue") == "done":
        # Already accepted — just ensure claimed_by is cleared
        if task.get("claimed_by"):
            from .db import update_task
            update_task(task_id, claimed_by=None)
        return True

    accept_completion(task_id, validator="human")

    # Verify
    task = get_task(task_id)
    if not task:
        print("  WARNING: task not found in DB after acceptance")
        return True  # non-fatal

    if task.get("queue") != "done":
        print(f"  WARNING: DB shows queue='{task.get('queue')}', fixing...")
        # Use update_task_queue to ensure side effects fire
        update_task_queue(
            task_id,
            "done",
            claimed_by=None,
            history_event="force_accepted",
            history_details="fixed inconsistent queue state",
        )
        return True

    if task.get("claimed_by"):
        from .db import update_task
        update_task(task_id, claimed_by=None)

    return True


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def approve_orchestrator_task(task_id_prefix: str) -> int:
    """Run the full approval flow. Returns 0 on success, non-zero on failure."""

    if not is_db_enabled():
        print("Error: Database mode required")
        return 1

    # Step 0: Resolve task
    print(f"Resolving task prefix '{task_id_prefix}'...")
    task_info = resolve_task_id(task_id_prefix)
    if not task_info:
        return 1

    task_id = task_info["id"]
    role = task_info["role"]
    queue = task_info["queue"]

    if role != "orchestrator_impl":
        print(f"Error: Task {task_id[:8]} has role='{role}', not 'orchestrator_impl'")
        print("Use approve_task.py for regular tasks")
        return 1

    # Allow re-running on 'done' tasks for idempotency
    if queue == "done":
        print(f"Task {task_id[:8]} is already in 'done' queue — verifying consistency.")
        accept_in_db(task_id)
        print(f"\nTask {task_id[:8]} confirmed done.")
        return 0

    if queue not in ("provisional", "review_pending", "claimed"):
        print(f"Error: Task {task_id[:8]} is in queue '{queue}', expected 'provisional', 'review_pending', or 'claimed'")
        return 1

    print(f"\nApproving orchestrator task {task_id[:8]} (queue={queue})")

    # Step 1: Check prerequisites
    repo = _repo_root()
    local_sub = _submodule_dir()

    branch = run(["git", "branch", "--show-current"], cwd=repo).stdout.strip()
    if branch != "main":
        print(f"Error: Must be on main branch (currently on '{branch}')")
        return 1

    sub_branch = run(
        ["git", "branch", "--show-current"], cwd=local_sub
    ).stdout.strip()
    if sub_branch != SUBMODULE_BRANCH:
        print(f"Error: Submodule must be on {SUBMODULE_BRANCH} (currently on '{sub_branch}')")
        return 1

    # Step 2: Find agent worktree
    print("\n1. Finding agent worktree...")
    agent_sub = find_agent_submodule(task_info)
    if not agent_sub:
        return 1
    print(f"   Agent submodule: {agent_sub}")

    # Step 3: Find agent commits
    print("\n2. Finding agent commits...")
    commits = find_agent_commits(agent_sub, local_sub)

    if not commits:
        print("   WARNING: No new commits found in agent submodule")
        print("   The agent may not have committed, or commits are already in main.")
        # Ask user to confirm
        try:
            response = input("   Continue anyway? [y/N] ").strip().lower()
        except EOFError:
            response = "n"
        if response != "y":
            print("   Aborted.")
            return 1
    else:
        print(f"   Found {len(commits)} commit(s) to cherry-pick")

    # Step 4: Cherry-pick
    if commits:
        print("\n3. Cherry-picking commits onto main...")
        if not cherry_pick_commits(commits, local_sub):
            return 1
        print("   Cherry-pick complete")

    # Step 5: Run tests
    if commits:
        print("\n4. Running tests...")
        if not run_tests(local_sub):
            # Revert the cherry-picks by resetting
            print("\n   Reverting cherry-picked commits due to test failure...")
            run(
                ["git", "reset", "--hard", f"HEAD~{len(commits)}"],
                cwd=local_sub,
                check=False,
            )
            print("   Reverted. Fix the tests and re-run.")
            return 1
        print("   Tests passed")
    else:
        print("\n3-4. Skipping cherry-pick and tests (no commits)")

    # Step 6: Push main
    if commits:
        print("\n5. Pushing main...")
        if not push_submodule(local_sub):
            return 1
        print("   Pushed")
    else:
        print("\n5. Skipping push (no commits)")

    # Step 7: Update submodule ref on main
    print("\n6. Updating submodule ref on main...")
    if not update_submodule_ref(task_id):
        return 1

    # Step 8: Accept in DB
    print(f"\n7. Accepting task {task_id[:8]} in DB...")
    accept_in_db(task_id)
    print("   Done")

    print(f"\nTask {task_id[:8]} approved and merged.")
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m orchestrator.approve_orch <task-id-prefix>")
        sys.exit(1)
    sys.exit(approve_orchestrator_task(sys.argv[1]))


if __name__ == "__main__":
    main()
