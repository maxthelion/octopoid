#!/usr/bin/env bash
# sweep-resources.sh — Manually trigger stale worktree and remote branch cleanup.
#
# Calls sweep_stale_resources() directly, bypassing the 30-minute scheduler
# interval. Useful for one-time cleanup of accumulated backlog.
#
# Usage:
#   scripts/sweep-resources.sh
#   scripts/sweep-resources.sh --grace 0    # Skip grace period (clean everything)
#
# The script must be run from the project root (where .octopoid/ lives).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

GRACE="${GRACE:-3600}"  # Default: 1 hour grace period

# Allow --grace flag to override
while [[ $# -gt 0 ]]; do
    case "$1" in
        --grace)
            GRACE="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: $0 [--grace SECONDS]" >&2
            exit 1
            ;;
    esac
done

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting manual resource sweep (grace=${GRACE}s)"

cd "$REPO_ROOT"

/opt/homebrew/bin/python3 - <<EOF
import sys
sys.path.insert(0, "$REPO_ROOT")

# Temporarily override grace period via monkeypatch
import orchestrator.scheduler as sched

_orig_sweep = sched.sweep_stale_resources

def _patched_sweep():
    """Sweep with overridden grace period."""
    import shutil
    from datetime import datetime, timezone
    from orchestrator import queue_utils
    from orchestrator.config import find_parent_project, get_tasks_dir, get_logs_dir
    from orchestrator.git_utils import run_git

    GRACE_PERIOD_SECONDS = $GRACE

    try:
        sdk = queue_utils.get_sdk()
        done_tasks = sdk.tasks.list(queue="done") or []
        failed_tasks = sdk.tasks.list(queue="failed") or []
    except Exception as e:
        print(f"ERROR: failed to fetch tasks: {e}", file=sys.stderr)
        return

    try:
        parent_repo = find_parent_project()
    except Exception as e:
        print(f"ERROR: could not find parent repo: {e}", file=sys.stderr)
        return

    tasks_dir = get_tasks_dir()
    logs_dir = get_logs_dir()
    now = datetime.now(timezone.utc)
    pruned_any = False
    worktrees_deleted = 0
    branches_deleted = 0

    for task in done_tasks + failed_tasks:
        task_id = task.get("id")
        queue = task.get("queue", "")
        if not task_id:
            continue

        ts_str = task.get("updated_at") or task.get("completed_at")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            elapsed = (now - ts).total_seconds()
        except (ValueError, TypeError):
            continue

        if elapsed < GRACE_PERIOD_SECONDS:
            continue

        task_dir = tasks_dir / task_id
        worktree_path = task_dir / "worktree"

        if worktree_path.exists():
            # Archive logs
            try:
                archive_dir = logs_dir / task_id
                archive_dir.mkdir(parents=True, exist_ok=True)
                for filename in ("stdout.log", "stderr.log", "result.json", "prompt.md"):
                    src = task_dir / filename
                    if src.exists():
                        shutil.copy2(src, archive_dir / filename)
            except Exception as e:
                print(f"WARN: failed to archive logs for {task_id}: {e}")

            # Remove worktree
            try:
                run_git(
                    ["worktree", "remove", "--force", str(worktree_path)],
                    cwd=parent_repo,
                    check=False,
                )
                if worktree_path.exists():
                    shutil.rmtree(worktree_path)
                pruned_any = True
                worktrees_deleted += 1
                print(f"  Swept worktree: {task_id} ({queue})")
            except Exception as e:
                print(f"WARN: failed to delete worktree for {task_id}: {e}")

        if queue == "done":
            branch = f"agent/{task_id}"
            try:
                result = run_git(
                    ["push", "origin", "--delete", branch],
                    cwd=parent_repo,
                    check=False,
                )
                if result.returncode == 0:
                    branches_deleted += 1
                    print(f"  Deleted remote branch: {branch}")
                else:
                    pass  # Already gone — non-fatal
            except Exception as e:
                print(f"WARN: failed to delete remote branch {branch}: {e}")

    if pruned_any:
        try:
            run_git(["worktree", "prune"], cwd=parent_repo, check=False)
        except Exception as e:
            print(f"WARN: git worktree prune failed: {e}")

    print(f"")
    print(f"Done. Worktrees deleted: {worktrees_deleted}, Remote branches deleted: {branches_deleted}")

_patched_sweep()
EOF
