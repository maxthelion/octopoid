# GH Issue Monitor Creating Duplicate Tasks for Completed Work

**Status:** Idea
**Captured:** 2026-02-15

## Raw

> investigate gh issue poller going rogue and creating tasks for completed work

## Idea

The GitHub issue monitor (`github_issue_monitor` role) is creating new tasks for issues that already have completed tasks in the done queue. For example, GH-9, GH-10, and GH-13 all had tasks completed and in done, but new tasks were created with different IDs (gh-9-2aca86e0, gh-10-da6082b5, gh-13-09b82489). The monitor has been disabled (`enabled: false` in agents.yaml) as a stopgap.

## Context

Discovered during the REFACTOR project pipeline work. The duplicate tasks were being claimed by agents and consuming implementer slots while REFACTOR-05 was running. The issues had already been addressed by prior tasks (gh-9-4502b83d, gh-10-d64a6f2c, gh-13-c2429634) which were in the done queue.

Likely cause: the monitor checks for open GitHub issues and creates tasks, but doesn't check whether a task for that issue already exists (or has been completed) in the queue. The task IDs include a hash suffix, so the same issue gets a different task ID each time.

## Open Questions

- Does the monitor check the done queue at all, or only incoming/claimed?
- Is the dedup logic based on issue number or task ID?
- Should dedup also check for closed GitHub issues before creating tasks?
- Were the issues still open on GitHub because the completion flow didn't close them? (Yes — confirmed for GH-9, GH-10, GH-13)

## Possible Next Steps

- Read `orchestrator/roles/github_issue_monitor.py` and trace the dedup logic
- Fix dedup to check all queues (including done) for existing tasks matching the GitHub issue number
- Add a hook to close GitHub issues when their tasks complete
- Re-enable the monitor once the fix is in place
