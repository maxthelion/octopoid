# Project: Testing Analyst Agent

**Source draft:** 87-2026-02-22-testing-analyst-agent.md
**Author:** human (via draft-idea)

## Summary

Create a background analyst agent that periodically reviews the testing landscape, identifies coverage gaps (especially missing integration/e2e tests), and proposes specific test improvements as drafts. Follows the codebase-analyst pattern.

## Design Decisions (from open questions)

1. **"Recently completed" = since last analysis run** — agent writes a timestamp on each run; the analysis script only looks at tasks completed after that timestamp.
2. **CI access via `gh` CLI** — agent can run `gh run list` and `gh run view` to check what's actually failing. Read-only, low-risk.
3. **Severity levels** — "no tests at all" = critical gap (P1), "unit tests only / over-mocked" = improvement (P2). Both flagged, different priority in proposals.
4. **Feature-to-test matching via grep** — search `tests/integration/` for references to changed files/functions. Content-based, not filename convention.
5. **No test quality checks in v1** — scope limited to coverage gaps. Quality checks (empty assertions, assert True) deferred to follow-up.

## Tasks (in dependency order)

1. **Create agent directory structure and config** (`testing-analyst-1-scaffold.md`)
2. **Write analysis scripts** (`testing-analyst-2-scripts.md`)
3. **Write agent prompt and instructions** (`testing-analyst-3-prompt.md`)
4. **Register job in jobs.yaml and test** (`testing-analyst-4-register.md`)
