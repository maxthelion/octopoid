# Agents Tab: Two-Tier Tabs for Flow Agents and Background Agents

**Status:** Idea
**Captured:** 2026-02-22
**Related:** Draft 79 (agents tab: flow + job agents), Draft 69 (codebase analyst agent)

## Raw

> We added jobs agents to the dashboard under agents. But these are the more scripty jobs. I wanted to see the code analyser etc. Can we move to the two tier tabs on this page with flow agents and background agents.

## Idea

The agents tab currently has two sections: flow agents (implementer, gatekeeper) and scheduler jobs (scripty cron-like tasks). But "jobs" are low-level scheduler internals — what's missing is visibility into background agents like the codebase analyst, draft aging agent, etc. These are autonomous agents that run periodically but aren't part of the task flow.

Restructure the agents tab with two sub-tabs:

- **Flow Agents** — agents that work on tasks through the flow system (implementer, gatekeeper). Show current task, status, last run, etc.
- **Background Agents** — autonomous agents that run periodically outside the flow (codebase analyst, draft processor, etc.). Show last run, interval, recent output/results.

The scheduler jobs section (added by draft 79) could either be folded into background agents or moved elsewhere — they're implementation details rather than things you'd want to monitor.

## Context

Draft 79 added job agents to the agents tab, but these are the low-level scheduler jobs (cache cleanup, heartbeat, etc.), not the higher-level background agents. The codebase analyst (draft 69) and similar autonomous agents need their own visibility. The current two-section layout (flow agents + jobs) doesn't surface these.

## Open Questions

- Where do scheduler jobs go? Hidden, or a third tab, or folded into background agents?
- What data should background agent entries show? (last run time, interval, recent results, status)
- How are background agents defined? Currently in `agents.yaml` — do they have a different config shape than flow agents?

## Possible Next Steps

- Refactor agents tab to use TabbedContent with "Flow Agents" and "Background Agents" sub-tabs
- Read background agent definitions from agents.yaml (non-flow agents)
- Display last run, interval, and recent output for each background agent
- Decide what to do with the jobs section from draft 79
