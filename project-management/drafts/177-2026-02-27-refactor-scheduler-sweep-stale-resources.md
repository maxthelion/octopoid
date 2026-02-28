# Refactor scheduler.sweep_stale_resources: extract _cleanup_stale_task to reduce CCN from 24 to ~8

**Author:** architecture-analyst
**Captured:** 2026-02-27

## Issue

`sweep_stale_resources` in `octopoid/scheduler.py` (lines 1820–1937) has CCN=24 and 118 NLOC.
The high complexity comes from embedding the full per-task cleanup pipeline — archive logs, remove
worktree, delete remote branch — directly inside the outer sweep loop. Each of the three operations
has its own `try/except` block for fault isolation, and these are all interleaved with loop control
logic (`pruned_any` flag, grace period checks). The result is a function with two distinct
responsibilities:

1. **Sweep coordination**: fetch done/failed tasks, check grace periods, track whether any pruning
   occurred, run `git worktree prune` at the end.
2. **Per-task cleanup**: archive logs to `runtime/logs/<id>/`, remove the worktree directory from
   both git tracking and the filesystem, delete the remote branch (done tasks only).

These two concerns should be separated. The cleanup logic for a single task is currently buried
inside a 118-line outer function, making it impossible to test independently and hard to read.

## Current Code

```python
def sweep_stale_resources() -> None:
    # ... 37 lines of setup (fetch tasks, dirs, grace constants) ...

    for task in done_tasks + failed_tasks:
        task_id = task.get("id")
        # grace period check (10 lines) ...

        task_dir = tasks_dir / task_id
        worktree_path = task_dir / "worktree"

        if worktree_path.exists():
            # Archive logs (try/except)
            try:
                archive_dir = logs_dir / task_id
                archive_dir.mkdir(parents=True, exist_ok=True)
                for filename in ("stdout.log", "stderr.log", "prompt.md"):
                    src = task_dir / filename
                    if src.exists():
                        shutil.copy2(src, archive_dir / filename)
            except Exception as e:
                logger.debug(f"...failed to archive logs for {task_id}: {e}")

            # Remove worktree (try/except)
            try:
                run_git(["worktree", "remove", "--force", str(worktree_path)], ...)
                if worktree_path.exists():
                    shutil.rmtree(worktree_path)
                pruned_any = True          # <-- outer-loop flag modified here
            except Exception as e:
                logger.debug(f"...failed to delete worktree for {task_id}: {e}")

        # Delete remote branch — done tasks only (try/except)
        if queue == "done":
            branch = f"agent/{task_id}"
            try:
                result = run_git(["push", "origin", "--delete", branch], ...)
                if result.returncode == 0:
                    logger.info(f"Deleted remote branch {branch}")
                else:
                    logger.debug(f"...remote branch {branch} deletion skipped: ...")
            except Exception as e:
                logger.debug(f"...failed to delete remote branch {branch}: {e}")

    if pruned_any:
        run_git(["worktree", "prune"], ...)
```

## Proposed Refactoring

Apply the **Extract Function** pattern to pull the per-task cleanup into
`_cleanup_stale_task(task_id, queue, task_dir, logs_dir, parent_repo) -> bool`.
The outer function becomes a coordination loop that delegates all per-task work to the helper.

```python
def _cleanup_stale_task(
    task_id: str,
    queue: str,
    task_dir: Path,
    logs_dir: Path,
    parent_repo: Path,
) -> bool:
    """Clean up resources for a single stale task.

    Returns True if the worktree was successfully removed (so the caller knows
    to run git worktree prune afterwards).
    """
    import shutil
    worktree_path = task_dir / "worktree"
    pruned = False

    if worktree_path.exists():
        # Archive logs before removing worktree
        try:
            archive_dir = logs_dir / task_id
            archive_dir.mkdir(parents=True, exist_ok=True)
            for filename in ("stdout.log", "stderr.log", "prompt.md"):
                src = task_dir / filename
                if src.exists():
                    shutil.copy2(src, archive_dir / filename)
        except Exception as e:
            logger.debug(f"sweep_stale_resources: failed to archive logs for {task_id}: {e}")

        # Remove worktree
        try:
            run_git(["worktree", "remove", "--force", str(worktree_path)],
                    cwd=parent_repo, check=False)
            if worktree_path.exists():
                shutil.rmtree(worktree_path)
            pruned = True
            logger.info(f"Swept worktree for task {task_id} ({queue})")
        except Exception as e:
            logger.debug(f"sweep_stale_resources: failed to delete worktree for {task_id}: {e}")

    # Delete remote branch for merged (done) tasks only
    if queue == "done":
        branch = f"agent/{task_id}"
        try:
            result = run_git(["push", "origin", "--delete", branch],
                             cwd=parent_repo, check=False)
            if result.returncode == 0:
                logger.info(f"Deleted remote branch {branch}")
            else:
                logger.debug(f"sweep_stale_resources: remote branch {branch} "
                             f"deletion skipped: {result.stderr.strip()}")
        except Exception as e:
            logger.debug(f"sweep_stale_resources: failed to delete remote branch {branch}: {e}")

    return pruned


def sweep_stale_resources() -> None:
    """Archive logs and delete worktrees for old done/failed tasks."""
    DONE_GRACE_SECONDS = 3600
    FAILED_GRACE_SECONDS = 86400

    try:
        sdk = queue_utils.get_sdk()
        done_tasks = sdk.tasks.list(queue="done") or []
        failed_tasks = sdk.tasks.list(queue="failed") or []
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
    pruned_any = False

    for task in done_tasks + failed_tasks:
        task_id = task.get("id")
        queue = task.get("queue", "")
        if not task_id:
            continue

        # Check grace period
        ts_str = task.get("updated_at") or task.get("completed_at")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            elapsed = (now - ts).total_seconds()
        except (ValueError, TypeError) as e:
            logger.debug(f"sweep_stale_resources: could not parse timestamp for {task_id}: {e}")
            continue

        grace = FAILED_GRACE_SECONDS if queue == "failed" else DONE_GRACE_SECONDS
        if elapsed < grace:
            continue

        task_dir = tasks_dir / task_id
        pruned_any |= _cleanup_stale_task(task_id, queue, task_dir, logs_dir, parent_repo)

    if pruned_any:
        try:
            run_git(["worktree", "prune"], cwd=parent_repo, check=False)
            logger.debug("sweep_stale_resources: ran git worktree prune")
        except Exception as e:
            logger.debug(f"sweep_stale_resources: git worktree prune failed: {e}")
```

## Why This Matters

- **Testability**: `_cleanup_stale_task` can be unit-tested with a mock filesystem and mock
  `run_git` — without needing to set up a full task list or run the sweep loop.
- **Readability**: `sweep_stale_resources` drops from 118 lines to ~40. The loop body becomes a
  single function call, making the coordination logic visible at a glance.
- **Maintainability**: When the cleanup policy changes (e.g. add a new file to archive, or change
  branch naming), the change is made in one isolated function rather than in the middle of a loop.
- **CCN reduction**: `sweep_stale_resources` drops from CCN=24 to ~8. `_cleanup_stale_task` has
  its own CCN of ~10 — correctly bounded since it has exactly one job.

## Metrics

- **File:** `octopoid/scheduler.py`
- **Function:** `sweep_stale_resources` (lines 1820–1937)
- **Current CCN:** 24 / Lines: 118
- **Estimated CCN after:** ~8 (outer) + ~10 (helper) — both within threshold
