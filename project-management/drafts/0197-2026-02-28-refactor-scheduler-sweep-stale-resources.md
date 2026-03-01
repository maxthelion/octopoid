# Refactor scheduler.sweep_stale_resources: extract per-task pipeline to reduce CCN from 24 to ~8

**Author:** architecture-analyst
**Captured:** 2026-02-28

## Issue

`sweep_stale_resources` in `octopoid/scheduler.py` (lines 1922–2039) has CCN 24 and 86 lines.
The function handles three distinct responsibilities in a single deeply nested loop:

1. **Grace-period gating** — parse the task's timestamp, compute elapsed time, skip if within the grace window
2. **Worktree cleanup** — archive stdout/stderr/prompt logs, then `git worktree remove` the directory
3. **Remote branch deletion** — `git push origin --delete agent/<id>` for `done` tasks only

All three phases are tangled inside one `for` loop with nested `if/try/except` blocks, making each phase difficult to read, test, or modify in isolation. A bug in timestamp parsing silently skips worktree deletion for that task; a failure in branch deletion is hard to distinguish from a grace-period skip in the logs.

## Current Code

```python
for task in done_tasks + failed_tasks:
    task_id = task.get("id")
    queue = task.get("queue", "")
    if not task_id:
        continue

    # Phase 1: timestamp gating (7 lines, 3 branches)
    ts_str = task.get("updated_at") or task.get("completed_at")
    if not ts_str:
        continue
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        elapsed = (now - ts).total_seconds()
    except (ValueError, TypeError) as e:
        logger.debug(...)
        continue
    grace = FAILED_GRACE_SECONDS if queue == "failed" else DONE_GRACE_SECONDS
    if elapsed < grace:
        continue

    # Phase 2: worktree cleanup (14 lines, 5 branches)
    worktree_path = task_dir / "worktree"
    if worktree_path.exists():
        try:
            ...archive logs...
        except Exception as e:
            ...
        try:
            ...remove worktree...
            pruned_any = True
        except Exception as e:
            ...

    # Phase 3: branch deletion (16 lines, 4 branches)
    if queue == "done":
        branch = f"agent/{task_id}"
        try:
            result = run_git(["push", "origin", "--delete", branch], ...)
            if result.returncode == 0:
                ...
            else:
                ...
        except Exception as e:
            ...
```

## Proposed Refactoring

Apply the **Extract Method** pattern (Fowler, *Refactoring* §6.1) to lift each phase into a focused helper function:

```python
def _task_past_grace(task: dict, now: datetime) -> bool:
    """Return True if task has exceeded its queue-dependent grace period."""
    ts_str = task.get("updated_at") or task.get("completed_at")
    if not ts_str:
        return False
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        elapsed = (now - ts).total_seconds()
    except (ValueError, TypeError):
        return False
    grace = 86400 if task.get("queue") == "failed" else 3600
    return elapsed >= grace


def _sweep_task_resources(
    task: dict,
    tasks_dir: Path,
    logs_dir: Path,
    parent_repo: Path,
) -> bool:
    """Archive logs and remove worktree for one task. Return True if worktree was removed."""
    task_id = task["id"]
    queue = task.get("queue", "")
    task_dir = tasks_dir / task_id
    worktree_path = task_dir / "worktree"
    swept = False

    if worktree_path.exists():
        try:
            archive_dir = logs_dir / task_id
            archive_dir.mkdir(parents=True, exist_ok=True)
            for filename in ("stdout.log", "stderr.log", "prompt.md"):
                src = task_dir / filename
                if src.exists():
                    shutil.copy2(src, archive_dir / filename)
        except Exception as e:
            logger.debug(f"sweep: failed to archive logs for {task_id}: {e}")

        try:
            run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=parent_repo, check=False)
            if worktree_path.exists():
                shutil.rmtree(worktree_path)
            swept = True
            logger.info(f"Swept worktree for task {task_id} ({queue})")
        except Exception as e:
            logger.debug(f"sweep: failed to delete worktree for {task_id}: {e}")

    if queue == "done":
        branch = f"agent/{task_id}"
        try:
            result = run_git(["push", "origin", "--delete", branch], cwd=parent_repo, check=False)
            if result.returncode == 0:
                logger.info(f"Deleted remote branch {branch}")
            else:
                logger.debug(f"sweep: remote branch {branch} deletion skipped: {result.stderr.strip()}")
        except Exception as e:
            logger.debug(f"sweep: failed to delete remote branch {branch}: {e}")

    return swept


def sweep_stale_resources() -> None:
    """Archive logs and delete worktrees for old done/failed tasks."""
    import shutil
    try:
        sdk = queue_utils.get_sdk()
        all_tasks = (sdk.tasks.list(queue="done") or []) + (sdk.tasks.list(queue="failed") or [])
    except Exception as e:
        logger.debug(f"sweep_stale_resources: failed to fetch tasks: {e}")
        return
    try:
        parent_repo = find_parent_project()
    except Exception as e:
        logger.debug(f"sweep_stale_resources: could not find parent repo: {e}")
        return

    tasks_dir = get_tasks_dir()
    logs_dir = get_logs_dir()
    now = datetime.now(timezone.utc)

    candidates = [t for t in all_tasks if t.get("id") and _task_past_grace(t, now)]
    pruned_any = any(_sweep_task_resources(t, tasks_dir, logs_dir, parent_repo) for t in candidates)

    if pruned_any:
        try:
            run_git(["worktree", "prune"], cwd=parent_repo, check=False)
        except Exception as e:
            logger.debug(f"sweep_stale_resources: git worktree prune failed: {e}")
```

## Why This Matters

- **Testability**: `_task_past_grace` and `_sweep_task_resources` can be unit-tested with a mock task dict and a temp directory — no scheduler harness needed.
- **Readability**: The main function collapses to ~20 lines with an obvious "fetch → filter → sweep → prune" pipeline. The nested 3-phase loop disappears.
- **Debuggability**: Failures in grace-period parsing, log archival, and branch deletion are now in separate call frames, making stack traces unambiguous.
- **Maintainability**: Adding a fourth cleanup phase (e.g. clearing database records) becomes a single call site addition, not another nested block.

## Metrics

- File: `octopoid/scheduler.py`
- Function: `sweep_stale_resources`
- Current CCN: 24 / Lines: 86
- Estimated CCN after: outer ~5, `_sweep_task_resources` ~8, `_task_past_grace` ~4
