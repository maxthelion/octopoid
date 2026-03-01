# Longer Worktree Retention for Failed Tasks

**Captured:** 2026-02-24

## Raw

> Let's adjust our sweeper of worktrees. A task failed last night in boxen. Its worktree was killed before we had a chance to investigate. The sweeper should leave worktrees for failed tasks for a longer period of time.

## Idea

The `sweep_stale_resources()` function in `orchestrator/scheduler.py` uses a single 1-hour grace period for both done and failed tasks. Failed task worktrees get deleted too quickly — before a human can investigate what went wrong.

Done tasks are safe to sweep quickly (their work is merged). But failed tasks need forensics: checking the worktree for partial work, reading logs, understanding what the agent did. A 1-hour window is too short, especially for overnight failures.

## Current Behaviour

`orchestrator/scheduler.py:1914` — `sweep_stale_resources()`:

- Single constant: `GRACE_PERIOD_SECONDS = 3600` (1 hour)
- Applied identically to both `done` and `failed` tasks
- After the grace period: archives logs, deletes worktree, prunes git worktrees
- Remote branches are only deleted for `done` tasks (failed branches are kept) — good

## Proposed Change

Use separate grace periods:

```python
DONE_GRACE_PERIOD_SECONDS = 3600       # 1 hour — work is merged, safe to clean
FAILED_GRACE_PERIOD_SECONDS = 86400    # 24 hours — need time to investigate
```

The fix is a ~5 line change in `sweep_stale_resources()`: pick the grace period based on the task's `queue` value.

## Context

A task failed overnight and by morning the worktree had already been swept. The logs were archived (to `.octopoid/runtime/logs/<task-id>/`) but the worktree — which contains the actual code changes, git history, and working state — was gone. This makes it much harder to understand what went wrong and whether any work is salvageable.

## Invariants

- `failed-worktree-retention`: Failed task worktrees are retained for at least 24 hours after the task enters the `failed` queue, giving humans time to investigate before cleanup. Done task worktrees may be cleaned up after 1 hour.

## Open Questions

- Is 24 hours the right retention for failed tasks, or should it be longer (48h? 72h)?
- Should the retention be configurable in `jobs.yaml` or `config.yaml`?
- Should we also consider a "pinned" mechanism where a human can mark a failed task's worktree as "don't sweep" for manual investigation?

## Possible Next Steps

- Change `GRACE_PERIOD_SECONDS` to two constants keyed on queue status
- Could be a quick task or just a direct fix — it's ~5 lines of code
