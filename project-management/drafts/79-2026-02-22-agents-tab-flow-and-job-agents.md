# Agents Tab: Show Flow Agents and Job Agents

**Status:** Idea
**Captured:** 2026-02-22

## Raw

> The agents tab in the dashboard should show both flow agents (implementer and sanity checker), as well as job agents such as proposers.

## Idea

The agents tab currently only shows flow agents (the ones defined in `agents.yaml` that claim tasks — implementers, sanity-check gatekeepers). It should also display job agents — background agents that run on a schedule to do things like propose drafts, analyze the codebase, or curate stale items. Both types should be visible in a single unified view.

## Context

The system has two kinds of agents: flow agents that participate in the task lifecycle (claim → implement → submit → review), and job agents that run periodically as scheduled jobs (proposers, analyzers, curators). The dashboard's agents tab only shows the former. As more job agents are added (draft 50, draft 69), the human needs visibility into all running agents — not just the task workers.

## Open Questions

- How are job agents registered? Do they appear in `agents.yaml` alongside flow agents, or in a separate config?
- What status information makes sense for job agents? (last run time, next scheduled run, recent output?)
- Should the two types be shown in separate sections/sub-tabs, or mixed in one list with a type indicator?
- Where does job agent status come from — the server, local process info, or both?

## Possible Next Steps

- Audit what agent types exist and where their config/status lives
- Design the agents tab layout to accommodate both types
- Enqueue as a task once the data model for job agents is clearer
