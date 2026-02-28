# Background agent observability and manual triggers

**Captured:** 2026-02-28

## Raw

> for the background agents, can they log their runs somewhere using the python logger. It should be possible to see on the agents tab of the dashboard what they've worked on. Also, it would be good if we could have a script for manually requesting them to work - eg skip the guard about whether they've already got work scheduled.

## Idea

Background agents (codebase analyst, draft aging, etc.) run on a schedule but are currently opaque — you can't see what they did on a given run from the dashboard. Two improvements:

1. **Run logging**: Each background agent run should log what it did via the Python logger, and the dashboard agents tab should surface this — e.g. "last run: 3m ago, processed 2 drafts" or "last run: 5m ago, no findings". This makes the agents tab useful for understanding what background agents are actually doing.

2. **Manual trigger script**: A script (or CLI command) to manually trigger a background agent run, bypassing the interval guard that prevents re-runs when work is already scheduled. Useful for testing, development, and "I want the analyst to run right now" situations.

## Invariants

- **background-agent-run-visibility**: Every background agent run produces a log entry visible in the dashboard agents tab, including what was processed and the outcome (even if "nothing to do").
- **manual-trigger-bypasses-guard**: A manual trigger command can force a background agent to run immediately, regardless of its interval timer or whether it has pending work.

## Context

Background agents were recently added (codebase analyst, draft aging agent) but their runs are invisible unless you tail the scheduler log. The dashboard agents tab shows agent config but not runtime activity. As more background agents are added, observability becomes important — you need to know whether they're doing useful work or silently failing.

## Open Questions

- Should run history be stored on the server (new endpoint) or just read from local logs?
- What's the right granularity — one log entry per run, or per-action within a run?
- Should the manual trigger be a CLI script, a `/trigger-agent` skill, or both?

## Possible Next Steps

- Add structured run logging to the background agent base class
- Store run summaries on the server (new `agent_runs` table or messages)
- Update dashboard agents tab to show recent run history
- Create `scripts/trigger-agent.sh` or equivalent
