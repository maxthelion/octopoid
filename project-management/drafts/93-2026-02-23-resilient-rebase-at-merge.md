# Make rebase-at-merge more resilient to concurrent main changes

**Status:** Idea
**Captured:** 2026-02-23

## Raw

> How do we make rebasing less fragile when branches are having stuff added to them? Use the recent example of the 2 tasks in failed to demonstrate.

## Idea

The `merge_pr` flow step rebases the agent's branch onto `origin/main` before merging. If the rebase hits conflicts, the entire task fails and gets sent back to incoming for re-implementation from scratch. This is wasteful — the agent's work was correct, it just can't be cleanly applied to the current main.

## Context

Two instances of TASK-e62966cb ("Add jobs.yaml and analyst agents to octopoid init") both completed successfully, were approved by the gatekeeper, but failed at `merge_pr`:

```
merge_pr failed: Rebase conflicts with origin/main. Agent will re-implement on a fresh base.
```

The conflicts were caused by us pushing directly to main (theme change, project_id fix, test fix) while the agents were working. The agents' changes to `init.py` and `README.md` conflicted with our direct pushes. Both attempts were thrown away and the tasks went back to incoming — even though the code was correct and just needed a manual conflict resolution.

The scheduler log shows the sequence clearly:
1. Agent completes work, pushed to `agent/c11040e2`
2. Gatekeeper reviews and approves
3. Flow runs `merge_pr` → rebase fails → task goes to `failed`
4. Task gets re-claimed, agent re-implements from scratch
5. Same thing happens again

In the end, we manually rebased the worktree, pushed, waited for CI, and merged — a 2-minute human fix for something the system burned two full agent runs on.

## Current behaviour

The `merge_pr` step in `orchestrator/steps.py`:
1. Fetches `origin/main`
2. Attempts `git rebase origin/main`
3. If conflicts → raises error → task fails → back to incoming
4. Agent re-implements from scratch on fresh base

## Problems

1. **All-or-nothing**: Any conflict, no matter how trivial, causes full re-implementation
2. **Wasted compute**: Two full agent runs (implement + gatekeeper) thrown away per attempt
3. **Race condition**: The more active main is, the more likely conflicts are — and the more likely the re-attempt also conflicts
4. **Human intervention needed anyway**: We ended up manually rebasing, which took 2 minutes

## Open Questions

- Should the system attempt automatic conflict resolution (e.g. accept theirs for CHANGELOG, retry rebase with strategies)?
- Should there be a "needs-rebase" state that pauses the task and notifies a human instead of re-implementing?
- Could we use merge commits instead of rebasing to avoid the problem entirely?
- Should we batch merges or use a merge queue to serialise PR landing?
- Is it worth having the agent attempt the rebase itself (it understands the code) rather than doing it mechanically?

## Possible Next Steps

- Add a `needs-rebase` queue state that holds tasks for manual resolution instead of full re-implementation
- Implement retry logic in `merge_pr`: attempt rebase, if trivial conflicts (CHANGELOG, README), auto-resolve, if complex conflicts, go to `needs-rebase`
- Consider a merge queue approach where PRs land sequentially, reducing the window for conflicts
- Add a "rebase-only" agent mode that takes a conflicted branch and resolves conflicts without re-implementing
