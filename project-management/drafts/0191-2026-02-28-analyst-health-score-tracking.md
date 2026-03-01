# Analyst Agents: Subjective Health Score Tracked Over Time

**Captured:** 2026-02-28

## Raw

> analyst agents should have some sort of subjective metric for the angle they are focusing on. When they write a draft, they should rate the codebase against that criteria. WE'd expect it to improve over time. If they have a directory, it could just be a log

## Idea

Each analyst agent (codebase-analyst, testing-analyst, architecture-analyst) should maintain a subjective health score for the dimension of the codebase they focus on. Every time an analyst runs and produces a draft, it also appends a scored entry to a log — a simple rating (e.g. 1-10) with a brief justification.

Over time, the log becomes a trend line: is code quality improving? Is test coverage trending up? Is architectural complexity getting better or worse? This gives the human a quick signal without reading every draft, and lets the system detect when improvements plateau or regress.

The score is intentionally subjective — the analyst weighs multiple quantitative signals (coverage %, MI, complexity, vulture hits) into a single holistic rating for its domain. Different analysts score different things:
- **Codebase analyst:** overall code health (coverage + complexity + dead code)
- **Testing analyst:** test adequacy (coverage gaps, missing integration tests, mock overuse)
- **Architecture analyst:** structural health (coupling, file sizes, abstraction quality)

Storage could be a simple append-only log file in the agent's directory, e.g. `.octopoid/agents/codebase-analyst/health-log.jsonl` with entries like:
```json
{"date": "2026-02-28", "score": 5, "summary": "62% coverage, scheduler MI=0, 27 vulture hits", "draft_id": 190}
```

## Invariants

- **health-score-logged**: Every analyst draft includes a numeric health score (1-10) for the analyst's domain
- **health-log-append-only**: Each analyst has a persistent log file that grows monotonically (entries are never deleted or modified)
- **score-has-justification**: Every score entry includes a brief text summary explaining the rating

## Context

The analyst agents now produce code quality drafts (draft 190 is the first from the updated codebase-analyst). The drafts contain good quantitative data but no longitudinal signal — you can't tell at a glance whether things are getting better or worse without comparing to previous reports manually.

## Open Questions

- Should the score be visible in the dashboard (e.g. a sparkline or trend indicator on the agents tab)?
- What scale? 1-10, 1-5, letter grades, or something else?
- Should the score feed into task prioritisation? (e.g. if testing score drops, auto-raise priority of test coverage tasks)
- Should we store the log on the server (via a new endpoint) or just as local files?

## Possible Next Steps

- Add a `health_score` field to the analyst prompt templates — instruct the agent to rate and justify
- Create a JSONL log file per analyst agent directory
- Update the analyst prompt/instructions to append to the log after writing a draft
- Optionally: add a dashboard widget showing score trends
