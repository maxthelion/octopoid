# Worktree Sweeper Process

**Status:** Idea
**Captured:** 2026-02-14

## Raw

> worktrees are swept up periodically by a sweeper process spawned by orchestrator. It should get rid of ones where the task associated has been done more than 1 hour ago. That gives us a bit of a grace period to go back in to figure stuff out if necessary. It might be work archiving the task logs for the moment in case we want to understnad more.

## Idea

Add a periodic sweeper process to the orchestrator that cleans up ephemeral worktrees. Rather than cleaning up immediately on task completion (which isn't happening reliably), the sweeper runs on a schedule and removes worktrees whose associated task has been in `done` or `failed` state for more than 1 hour.

The 1-hour grace period lets operators inspect worktrees for debugging before they disappear. Task logs (stdout.log, stderr.log, result.json, prompt.md) should be archived before deletion so we can understand what happened later.

## Context

Currently there is no worktree cleanup in the scheduler's agent completion flow — `handle_agent_result()` transitions task state but never calls `cleanup_task_worktree()`. The only cleanup is the manual `octopoid worktrees-clean` CLI command. Worktrees accumulate and require manual `git worktree remove --force` and `git worktree prune`. This has been a recurring pain point during today's session.

The original ephemeral worktrees design doc (037) specified cleanup on task completion but it was never wired up in the scheduler.

## Open Questions

- Where should archived logs live? `.octopoid/runtime/logs/tasks/<task-id>/` or similar?
- Should the sweeper be its own agent role, or a function called by the scheduler on each tick?
- What about worktrees for tasks stuck in `claimed` with expired leases and dead processes?
- Should `octopoid worktrees-clean` be updated to respect the 1-hour grace period too?

## Possible Next Steps

- Add a `sweep_worktrees()` function to the scheduler that runs on each tick
- Query the server for tasks in `done`/`failed` with `completed_at` > 1 hour ago
- Archive task logs before deleting the worktree directory
- Clean up the git worktree reference and prune
