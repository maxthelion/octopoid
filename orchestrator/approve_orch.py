"""Manual fallback for approving orchestrator specialist tasks.

NOTE: As of 2026-02-08, orchestrator_impl agents self-merge their work
to main when pytest passes (see OrchestratorImplRole._try_merge_to_main).
This script is now primarily used for:
- Manual approval of tasks where self-merge failed (test failures, conflicts)
- Re-running approval after manually fixing conflicts
- Recovering from partial failures (e.g., push failed but merge succeeded)

Uses the push-to-origin pattern: all git operations happen in the agent's
worktree. The human's local checkout is never modified. After rebasing onto
origin/main in the agent's worktree, we push to origin via refspec.

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


def _resolve_agent_name(task_info: dict[str, Any]) -> str | None:
    """Resolve the agent name from task info or claim history."""
    agent_name = task_info.get("claimed_by")
    if not agent_name:
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
    return agent_name


def find_agent_submodule(task_info: dict[str, Any]) -> Path | None:
    """Find the orchestrator submodule inside the agent's worktree.

    The agent name comes from task.claimed_by (still set for provisional
    tasks). The worktree lives at .orchestrator/agents/<name>/worktree/
    and its orchestrator submodule is in the ``orchestrator/`` sub-dir.

    Returns the absolute path to the submodule directory, or None.
    """
    agent_name = _resolve_agent_name(task_info)

    if not agent_name:
        print("ERROR: Cannot determine agent name (claimed_by is empty and no claim history)")
        return None

    worktree_sub = get_agents_runtime_dir() / agent_name / "worktree" / "orchestrator"
    if not worktree_sub.exists():
        print(f"ERROR: Agent worktree submodule not found at {worktree_sub}")
        return None

    return worktree_sub


def find_agent_worktree(task_info: dict[str, Any]) -> Path | None:
    """Find the agent's worktree root (main repo checkout).

    Returns the absolute path to the worktree root, or None.
    """
    agent_name = _resolve_agent_name(task_info)
    if not agent_name:
        print("ERROR: Cannot determine agent name")
        return None

    worktree = get_agents_runtime_dir() / agent_name / "worktree"
    if not worktree.exists():
        print(f"ERROR: Agent worktree not found at {worktree}")
        return None

    return worktree


# ---------------------------------------------------------------------------
# Step 2 — Find agent branches
# ---------------------------------------------------------------------------


def find_submodule_branch(agent_sub: Path, task_id: str) -> str | None:
    """Find the orch/<task-id> branch in the agent's submodule.

    Returns the branch name or None.
    """
    task_branch = f"orch/{task_id}"
    check = run(
        ["git", "rev-parse", "--verify", task_branch],
        cwd=agent_sub, check=False,
    )
    if check.returncode == 0:
        print(f"  Agent submodule branch: {task_branch}")
        return task_branch

    # Fallback: detect the agent's current branch
    result = run(
        ["git", "branch", "--show-current"], cwd=agent_sub, check=False
    )
    branch = result.stdout.strip() if result.returncode == 0 else ""
    if branch and branch != SUBMODULE_BRANCH:
        print(f"  Agent submodule branch (current): {branch}")
        return branch

    return None


def find_main_repo_branch(agent_worktree: Path, task_id: str) -> str | None:
    """Find the tooling/<task-id> or agent/<task-id>-* branch in agent worktree.

    Returns the branch name or None.
    """
    # Check tooling/<task-id> first (current convention)
    for pattern in [f"tooling/{task_id}*", f"agent/{task_id}-*"]:
        result = run(
            ["git", "branch", "--list", pattern],
            cwd=agent_worktree,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            branch = result.stdout.strip().lstrip("* ").split("\n")[0].strip()
            print(f"  Agent main repo branch: {branch}")
            return branch

    return None


def count_branch_commits(cwd: Path, branch: str, base: str = "origin/main") -> int:
    """Count commits on branch not in base."""
    run(["git", "fetch", "origin", "main"], cwd=cwd, check=False)
    result = run(
        ["git", "rev-list", "--count", f"{base}..{branch}"],
        cwd=cwd, check=False,
    )
    if result.returncode == 0:
        return int(result.stdout.strip())
    return 0


# ---------------------------------------------------------------------------
# Step 3 — Rebase onto origin/main
# ---------------------------------------------------------------------------


def rebase_onto_origin(cwd: Path, branch: str) -> bool:
    """Checkout branch, fetch origin/main, and rebase onto it.

    All operations happen in the agent's worktree (cwd). Returns True on
    success. On conflict, aborts the rebase and returns False.
    """
    run(["git", "fetch", "origin", "main"], cwd=cwd, check=False)

    result = run(["git", "checkout", branch], cwd=cwd, check=False)
    if result.returncode != 0:
        print(f"  ERROR: checkout {branch} failed: {result.stderr.strip()}")
        return False

    print(f"  Rebasing {branch} onto origin/main...")
    result = run(["git", "rebase", "origin/main"], cwd=cwd, check=False)
    if result.returncode != 0:
        print(f"  CONFLICT during rebase: {result.stderr.strip()}")
        run(["git", "rebase", "--abort"], cwd=cwd, check=False)
        return False

    return True


# ---------------------------------------------------------------------------
# Step 4 — Run tests (in agent worktree)
# ---------------------------------------------------------------------------


def _find_venv_python(agent_sub: Path) -> Path | None:
    """Find the Python executable for running tests.

    Searches (in order):
    1. agent_sub/venv/bin/python
    2. <repo_root>/.orchestrator/venv/bin/python
    3. <submodule_dir>/venv/bin/python

    Returns the path or None if not found.
    """
    venv_python = agent_sub / "venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python
    venv_python = _repo_root() / ".orchestrator" / "venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python
    venv_python = _submodule_dir() / "venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python
    return None


def _run_pytest(venv_python: Path, cwd: Path) -> subprocess.CompletedProcess:
    """Run pytest and return the CompletedProcess."""
    return run(
        [str(venv_python), "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=cwd,
        check=False,
        timeout=300,
    )


def parse_test_failures(pytest_output: str) -> set[str]:
    """Extract the set of FAILED test node IDs from pytest verbose output.

    Looks for lines matching ``FAILED tests/test_foo.py::TestBar::test_baz``
    in the short test summary section, or lines containing ``FAILED`` in the
    per-test result lines.

    Returns a set of test node ID strings (e.g.
    ``{"tests/test_foo.py::TestBar::test_baz"}``).
    """
    import re
    failures: set[str] = set()
    for line in pytest_output.splitlines():
        # Match "FAILED tests/..." lines from the summary section
        m = re.match(r"^FAILED\s+(\S+)", line.strip())
        if m:
            failures.add(m.group(1))
            continue
        # Match verbose-mode lines like "tests/test_foo.py::test_bar FAILED"
        m = re.match(r"^(tests/\S+::\S+)\s+FAILED", line.strip())
        if m:
            failures.add(m.group(1))
    return failures


def run_tests(agent_sub: Path) -> bool:
    """Run pytest in the agent's submodule worktree.

    Returns True if tests pass, False otherwise.

    Note: This is the simple version that does NOT compare against a
    baseline.  Use ``run_tests_with_baseline()`` for baseline-aware
    approval.
    """
    venv_python = _find_venv_python(agent_sub)
    if not venv_python:
        print("  WARNING: No venv found, skipping tests")
        return True

    print("  Running tests...")
    result = _run_pytest(venv_python, agent_sub)

    if result.returncode != 0:
        print(f"\n  Tests FAILED (exit code {result.returncode})")
        lines = result.stdout.strip().splitlines()
        tail = lines[-30:] if len(lines) > 30 else lines
        print("\n  " + "\n  ".join(tail))
        return False

    for line in result.stdout.splitlines():
        if "passed" in line:
            print(f"  {line.strip()}")
            break

    return True


def run_tests_with_baseline(agent_sub: Path, branch: str) -> bool:
    """Run pytest on origin/main (baseline) then on the agent branch.

    Only NEW test failures (not present in the baseline) block approval.
    Pre-existing failures on main are reported but tolerated.

    Args:
        agent_sub: Path to the agent's submodule worktree.
        branch: The agent's feature branch (already checked out).

    Returns True if there are no NEW failures compared to baseline.
    """
    venv_python = _find_venv_python(agent_sub)
    if not venv_python:
        print("  WARNING: No venv found, skipping tests")
        return True

    # --- Step 1: Capture baseline failures on origin/main ---
    print("  4a. Capturing baseline test results on origin/main...")
    # Temporarily check out origin/main
    stash = run(["git", "stash"], cwd=agent_sub, check=False)
    checkout_main = run(
        ["git", "checkout", "origin/main", "--detach"],
        cwd=agent_sub, check=False,
    )
    if checkout_main.returncode != 0:
        print(f"  WARNING: Cannot checkout origin/main for baseline: {checkout_main.stderr.strip()}")
        print("  Falling back to simple test run (no baseline comparison).")
        # Restore branch
        run(["git", "checkout", branch], cwd=agent_sub, check=False)
        if stash.returncode == 0 and "No local changes" not in stash.stdout:
            run(["git", "stash", "pop"], cwd=agent_sub, check=False)
        return run_tests(agent_sub)

    baseline_result = _run_pytest(venv_python, agent_sub)
    baseline_failures = parse_test_failures(baseline_result.stdout)

    if baseline_failures:
        print(f"  Baseline (origin/main): {len(baseline_failures)} pre-existing failure(s)")
        for f in sorted(baseline_failures):
            print(f"    [baseline] {f}")
    else:
        print("  Baseline (origin/main): all tests pass")

    # --- Step 2: Restore agent branch and run tests ---
    print(f"  4b. Running tests on {branch}...")
    run(["git", "checkout", branch], cwd=agent_sub, check=False)
    if stash.returncode == 0 and "No local changes" not in stash.stdout:
        run(["git", "stash", "pop"], cwd=agent_sub, check=False)

    branch_result = _run_pytest(venv_python, agent_sub)
    branch_failures = parse_test_failures(branch_result.stdout)

    # --- Step 3: Compare ---
    new_failures = branch_failures - baseline_failures
    fixed_in_branch = baseline_failures - branch_failures

    if fixed_in_branch:
        print(f"  Branch fixes {len(fixed_in_branch)} previously-failing test(s)")

    if new_failures:
        print(f"\n  NEW test failures ({len(new_failures)}):")
        for f in sorted(new_failures):
            print(f"    [NEW] {f}")
        # Show tail of output for debugging
        lines = branch_result.stdout.strip().splitlines()
        tail = lines[-20:] if len(lines) > 20 else lines
        print("\n  " + "\n  ".join(tail))
        return False

    if branch_failures:
        print(f"  Branch has {len(branch_failures)} failure(s), all pre-existing. Approved.")
    else:
        # Print summary line
        for line in branch_result.stdout.splitlines():
            if "passed" in line:
                print(f"  {line.strip()}")
                break

    return True


# ---------------------------------------------------------------------------
# Step 5 — Push to origin via refspec
# ---------------------------------------------------------------------------


def push_to_origin(cwd: Path, branch: str, target: str = "main") -> bool:
    """Push branch to origin/target via refspec (ff-only).

    First pushes the branch itself (for traceability), then pushes
    branch:target. Retries once on failure (re-fetch, re-rebase).

    Returns True on success.
    """
    # Push the branch itself first
    result = run(
        ["git", "push", "origin", branch, "--force-with-lease"],
        cwd=cwd, check=False,
    )
    if result.returncode != 0:
        print(f"  WARNING: push {branch} failed: {result.stderr.strip()}")
        # Non-fatal — the refspec push is what matters

    # Push branch:target (ff-only since we just rebased)
    for attempt in range(2):
        result = run(
            ["git", "push", "origin", f"{branch}:{target}"],
            cwd=cwd, check=False,
        )
        if result.returncode == 0:
            break

        if attempt == 0:
            print(f"  Push {branch}:{target} failed, rebasing and retrying...")
            run(["git", "fetch", "origin", "main"], cwd=cwd, check=False)
            rebase = run(["git", "rebase", "origin/main"], cwd=cwd, check=False)
            if rebase.returncode != 0:
                print(f"  Retry rebase failed: {rebase.stderr.strip()}")
                run(["git", "rebase", "--abort"], cwd=cwd, check=False)
                return False
            run(
                ["git", "push", "origin", branch, "--force-with-lease"],
                cwd=cwd, check=False,
            )
        else:
            print(f"  Push failed after retry: {result.stderr.strip()}")
            return False

    # Clean up remote branch
    run(["git", "push", "origin", "--delete", branch], cwd=cwd, check=False)
    return True


# ---------------------------------------------------------------------------
# Step 7 — Accept in DB
# ---------------------------------------------------------------------------


def accept_in_db(task_id: str) -> bool:
    """Move task to done, clear claimed_by, unblock dependents.

    Idempotent — safe to call multiple times.  If the task is already
    in the 'done' queue, just ensures claimed_by is cleared.

    Uses update_task_queue() to guarantee side effects (unblocking
    dependents, clearing claimed_by) are always applied.  All error paths
    use update_task_queue() so that raw SQL is never needed.
    """
    # Check if already done to avoid duplicate history entries
    try:
        task = get_task(task_id)
    except Exception as exc:
        print(f"  WARNING: get_task() failed: {exc}")
        print("  Attempting update_task_queue() directly...")
        try:
            update_task_queue(
                task_id,
                "done",
                claimed_by=None,
                history_event="force_accepted",
                history_details=f"get_task failed: {exc}",
            )
            return True
        except Exception as exc2:
            print(f"  ERROR: update_task_queue() also failed: {exc2}")
            return False

    if task and task.get("queue") == "done":
        # Already accepted — just ensure claimed_by is cleared
        if task.get("claimed_by"):
            from .db import update_task
            update_task(task_id, claimed_by=None)
        return True

    try:
        accept_completion(task_id, accepted_by="human")
    except Exception as exc:
        print(f"  WARNING: accept_completion() failed: {exc}")
        print("  Falling back to update_task_queue()...")
        try:
            update_task_queue(
                task_id,
                "done",
                claimed_by=None,
                history_event="force_accepted",
                history_details=f"accept_completion failed: {exc}",
            )
        except Exception as exc2:
            print(f"  ERROR: update_task_queue() also failed: {exc2}")
            return False

    # Verify
    try:
        task = get_task(task_id)
    except Exception as exc:
        print(f"  WARNING: verification get_task() failed: {exc}")
        return True  # non-fatal — we already called accept_completion

    if not task:
        print("  WARNING: task not found in DB after acceptance")
        return True  # non-fatal

    if task.get("queue") != "done":
        print(f"  WARNING: DB shows queue='{task.get('queue')}', fixing...")
        try:
            update_task_queue(
                task_id,
                "done",
                claimed_by=None,
                history_event="force_accepted",
                history_details="fixed inconsistent queue state",
            )
        except Exception as exc:
            print(f"  ERROR: force fix failed: {exc}")
            return False
        return True

    if task.get("claimed_by"):
        from .db import update_task
        update_task(task_id, claimed_by=None)

    return True


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def approve_orchestrator_task(task_id_prefix: str) -> int:
    """Run the full approval flow using push-to-origin.

    All git operations happen in the agent's worktree. The human's local
    checkout is never modified. Returns 0 on success, non-zero on failure.
    """

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

    # Step 1: Find agent worktrees
    print("\n1. Finding agent worktree...")
    agent_sub = find_agent_submodule(task_info)
    if not agent_sub:
        return 1
    print(f"   Agent submodule: {agent_sub}")

    agent_worktree = find_agent_worktree(task_info)

    # Step 2: Find agent branches
    print("\n2. Finding agent branches...")
    sub_branch = find_submodule_branch(agent_sub, task_id)
    main_branch = None
    if agent_worktree:
        main_branch = find_main_repo_branch(agent_worktree, task_id)

    has_sub = sub_branch is not None
    has_main = main_branch is not None

    if has_sub:
        n = count_branch_commits(agent_sub, sub_branch)
        print(f"   Submodule: {n} commit(s) on {sub_branch}")
    else:
        print("   No submodule branch found")

    if has_main:
        n = count_branch_commits(agent_worktree, main_branch)
        print(f"   Main repo: {n} commit(s) on {main_branch}")
    else:
        print("   No main repo branch found")

    if not has_sub and not has_main:
        print("\n   WARNING: No branches found in submodule or main repo")
        print("   The agent may not have committed, or commits are already merged.")
        try:
            response = input("   Continue anyway? [y/N] ").strip().lower()
        except EOFError:
            response = "n"
        if response != "y":
            print("   Aborted.")
            return 1

    # Step 3: Rebase onto origin/main (in agent worktree)
    if has_sub:
        print(f"\n3. Rebasing submodule {sub_branch} onto origin/main...")
        if not rebase_onto_origin(agent_sub, sub_branch):
            return 1
        print("   Rebased")

    if has_main:
        print(f"\n3b. Rebasing main repo {main_branch} onto origin/main...")
        if not rebase_onto_origin(agent_worktree, main_branch):
            return 1
        print("   Rebased")

    # Step 4: Run tests with baseline comparison (in agent worktree)
    if has_sub:
        print("\n4. Running tests with baseline comparison...")
        if not run_tests_with_baseline(agent_sub, sub_branch):
            print("   NEW test failures detected. Fix and re-run.")
            return 1
        print("   Tests approved (no new failures)")
    else:
        print("\n4. Skipping tests (no submodule commits)")

    # Step 5: Push to origin
    if has_sub:
        print(f"\n5. Pushing {sub_branch} to origin/main (submodule)...")
        if not push_to_origin(agent_sub, sub_branch):
            return 1
        print("   Pushed")

    if has_main:
        print(f"\n5b. Pushing {main_branch} to origin/main (main repo)...")
        if not push_to_origin(agent_worktree, main_branch):
            return 1
        print("   Pushed")

    # Step 6: Accept in DB
    print(f"\n6. Accepting task {task_id[:8]} in DB...")
    accept_in_db(task_id)
    print("   Done")

    if has_sub or has_main:
        print(f"\nTask {task_id[:8]} approved and pushed to origin.")
        print("Run `git pull` (and `cd orchestrator && git pull` for submodule) to update local.")
    else:
        print(f"\nTask {task_id[:8]} accepted (no commits to push).")
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
