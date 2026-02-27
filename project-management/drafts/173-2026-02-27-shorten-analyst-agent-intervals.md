# Shorten analyst agent intervals — guard script already prevents duplicates

**Captured:** 2026-02-27

## Raw

> The analyst agents should run on a shorter schedule, but should be limited by whether they've created a draft. At the moment, if we implement one of their drafts, we have to wait up to 24 hours to get a new suggestion.

## Idea

The three analyst agents (testing-analyst, architecture-analyst, codebase-analyst) all run with `interval_seconds: 86400` (24 hours). Each one already has a `guard.sh` that checks for pending drafts with `status=idea` by that author — if one exists, the agent outputs `SKIP` and exits immediately without doing any work.

This means the guard script is already the rate limiter, not the interval. We can safely drop the interval to something much shorter (e.g. 2–4 hours) because:

1. If a pending proposal exists → guard fires, agent exits in seconds, minimal cost
2. If the proposal was acted on (implemented, dismissed, superseded) → the guard passes and the agent runs, producing a new suggestion within hours instead of waiting until tomorrow

The current 24h interval means there's always a dead period after a draft is acted on where no new suggestions come in, even though the system is ready for them.

## Context

We implemented draft 92 (SDK claim tests) which was proposed by the testing-analyst. After that, no new testing suggestion appeared because the analyst won't run again for up to 24 hours. Same pattern applies to architecture-analyst and codebase-analyst proposals.

## Open Questions

- What interval is right? 2 hours? 4 hours? The guard makes it cheap to run frequently, but each invocation still costs a haiku/sonnet call if the guard passes.
- Should the guard also check for `in_progress` drafts (not just `idea`)? If a draft has been enqueued as a task but the task isn't done yet, the analyst might propose something in the same area.

## Possible Next Steps

- Change `interval_seconds` from `86400` to `7200` (2h) or `14400` (4h) in all three agent.yaml files
- Optionally update guard.sh to also skip when drafts are `in_progress`
- This is a config-only change — no code changes needed
