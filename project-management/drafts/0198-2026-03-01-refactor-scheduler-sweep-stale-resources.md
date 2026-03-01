# Refactor scheduler.sweep_stale_resources: extract Pipeline steps to reduce CCN from 24 to ~5

**Author:** architecture-analyst
**Captured:** 2026-03-01

## Issue

`sweep_stale_resources` in `octopoid/scheduler.py` (lines 1922–2040) has a cyclomatic complexity of **24** and is 118 lines long. The function performs three distinct, sequential cleanup operations for each stale task — all deeply inlined with nested try/except blocks:

1. **Archive logs** — copy stdout.log, stderr.log, prompt.md to the archive directory
2. **Remove worktree** — call `git worktree remove --force` and `shutil.rmtree`
3. **Delete remote branch** — call `git push origin --delete <branch>` (done tasks only)

Each of these is wrapped in its own try/except, and the whole thing is nested inside a for-loop with timestamp parsing, grace-period checking, and queue-type branching. The result is 5+ levels of nesting and a CCN that is 60% over the acceptable threshold.

## Current Code

```python
# 118-line monolith — 3 operations per task inlined with nested try/excepts
for task in done_tasks + failed_tasks:
    ...
    # 1. Check age / grace period (10 lines)
    ts = datetime.fromisoformat(...)
    elapsed = (now - ts).total_seconds()
    grace = FAILED_GRACE_SECONDS if queue == "failed" else DONE_GRACE_SECONDS
    if elapsed < grace:
        continue

    # 2. Archive logs (try/except, 8 lines)
    if worktree_path.exists():
        try:
            archive_dir = logs_dir / task_id
            archive_dir.mkdir(parents=True, exist_ok=True)
            for filename in ("stdout.log", "stderr.log", "prompt.md"):
                src = task_dir / filename
                if src.exists():
                    shutil.copy2(src, archive_dir / filename)
        except Exception as e: ...

        # 3. Remove worktree (try/except, 10 lines, inside same `if`)
        try:
            run_git(["worktree", "remove", "--force", str(worktree_path)], ...)
            if worktree_path.exists():
                shutil.rmtree(worktree_path)
            pruned_any = True
        except Exception as e: ...

    # 4. Delete remote branch for done tasks (try/except, 12 lines)
    if queue == "done":
        branch = f"agent/{task_id}"
        try:
            result = run_git(["push", "origin", "--delete", branch], ...)
            if result.returncode == 0: ...
            else: ...
        except Exception as e: ...
```

## Proposed Refactoring

Apply the **Extract Method** pattern to pull each sub-operation into its own focused function. The main loop becomes an orchestration pipeline — each step is independently testable and readable.

```python
def _archive_task_logs(task_id: str, task_dir: Path, logs_dir: Path) -> None:
    """Copy stdout.log, stderr.log, prompt.md to the archive directory."""
    try:
        archive_dir = logs_dir / task_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        for filename in ("stdout.log", "stderr.log", "prompt.md"):
            src = task_dir / filename
            if src.exists():
                shutil.copy2(src, archive_dir / filename)
    except Exception as e:
        logger.debug(f"sweep_stale_resources: failed to archive logs for {task_id}: {e}")


def _remove_task_worktree(task_id: str, worktree_path: Path, parent_repo: Path) -> bool:
    """Remove worktree from git tracking and filesystem. Returns True if removed."""
    try:
        run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=parent_repo, check=False)
        if worktree_path.exists():
            shutil.rmtree(worktree_path)
        logger.info(f"Swept worktree for task {task_id}")
        return True
    except Exception as e:
        logger.debug(f"sweep_stale_resources: failed to delete worktree for {task_id}: {e}")
        return False


def _delete_task_remote_branch(task_id: str, parent_repo: Path) -> None:
    """Delete the remote agent/<task_id> branch (idempotent, non-fatal)."""
    branch = f"agent/{task_id}"
    try:
        result = run_git(["push", "origin", "--delete", branch], cwd=parent_repo, check=False)
        if result.returncode == 0:
            logger.info(f"Deleted remote branch {branch}")
        else:
            logger.debug(f"Remote branch {branch} deletion skipped: {result.stderr.strip()}")
    except Exception as e:
        logger.debug(f"sweep_stale_resources: failed to delete remote branch {branch}: {e}")


def sweep_stale_resources() -> None:
    """Archive logs and delete worktrees for old done/failed tasks."""
    ...
    for task in done_tasks + failed_tasks:
        ...  # timestamp parse + grace period check (unchanged)
        if worktree_path.exists():
            _archive_task_logs(task_id, task_dir, logs_dir)
            if _remove_task_worktree(task_id, worktree_path, parent_repo):
                pruned_any = True
        if queue == "done":
            _delete_task_remote_branch(task_id, parent_repo)
```

## Why This Matters

- **Testability:** Each extracted helper can be unit-tested in isolation with a mock filesystem and mock `run_git`. Currently, the only way to test branch deletion is to run the entire sweep logic.
- **Readability:** The main loop drops from ~80 lines of nested try/excepts to ~15 lines of sequential calls — immediately readable as a pipeline.
- **Debuggability:** When a cleanup step fails, the stack trace will point to a named function (`_remove_task_worktree`) rather than an anonymous inner block.
- **Maintainability:** Adding a new cleanup step (e.g. expiring API resources) is a one-liner addition to the pipeline rather than another nested try/except block.

## Metrics

- File: `octopoid/scheduler.py`
- Function: `sweep_stale_resources`
- Lines: 1922–2040
- Current CCN: 24 / NLOC: 86 / length: 118
- Estimated CCN after: ~5 for main function, ~3–4 each for the three helpers
