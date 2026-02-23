---
**Processed:** 2026-02-23
**Mode:** human-guided
**Actions taken:**
- Resolved all 5 open questions with human approval
- Proposed 4 implementation tasks as a project (sequential dependency chain)
- Proposed tasks written to `project-management/drafts/proposed-tasks/testing-analyst-*.md`
**Outstanding items:** Proposed tasks awaiting enqueue decision
---

# Testing Analyst Agent

**Status:** In Progress
**Captured:** 2026-02-22

## Raw

> Let's add some more analysts. One should be focusing on testing. They should identify gaps in testing functionality, especially looking at tasks that have been done recently. They should embody the idea of outside in testing. They should also look at where agents have written unit tests only and are too granular, missing how the big picture comes together.

## Idea

A background analyst agent (like the codebase analyst, Draft 69) that periodically reviews the testing landscape. It runs on a schedule, analyses recently completed tasks and their test coverage, and proposes improvements.

### What it looks for

1. **Test coverage gaps after recent work** — scans recently completed tasks (done queue), checks what files were changed (via PR diffs or commit history), and identifies whether adequate tests exist for those changes. Flags features that shipped with no tests or only superficial ones.

2. **Outside-in testing gaps** — looks for features that have unit tests but no integration or end-to-end test covering the full path. The testing philosophy (docs/testing.md) prioritises e2e tests with a real server over mocked unit tests. This agent enforces that by finding cases where agents wrote granular unit tests that mock everything but miss the big picture.

3. **Over-mocked tests** — identifies tests that mock so heavily they're testing the mocks, not the code. Common agent pattern: mock get_sdk(), mock every dependency, assert the mock was called. These tests pass but prove nothing about real behaviour.

4. **Missing integration test scenarios** — compares the feature set (from CHANGELOG, drafts, or task history) against what's actually tested in tests/integration/. Proposes specific test scenarios that should exist.

### How it works

Same pattern as codebase analyst:
- Guard script: skip if there's already an unresolved testing proposal draft
- Analysis script: scan recent done tasks, check test coverage, find gaps
- Agent reads the report, picks the most impactful gap, writes a draft proposing specific tests
- Attaches actions (Enqueue test task / Dismiss) and posts to inbox

### Agent config

```yaml
# .octopoid/agents/testing-analyst/agent.yaml
role: analyse
model: sonnet
max_turns: 30
interval_seconds: 86400  # daily
spawn_mode: scripts
lightweight: true
allowed_tools:
  - Read
  - Glob
  - Grep
  - Bash
```

## Context

Came up during the draft-50 merge readiness assessment (Draft 86). Found significant integration test gaps — 17 CI failures, no tests for new dashboard tabs, no e2e test for the action system pipeline. Agents tend to write unit tests that mock everything because it's fast and passes, but these tests don't catch real integration issues (like the server submodule being out of sync, or a deleted directory still being referenced).

The codebase analyst (Draft 69) already exists as a model for this kind of periodic analysis agent. This extends the pattern to testing.

## Open Questions (Resolved)

- **"Recently completed" = since last analysis run.** Agent writes a timestamp file on each run; analysis script only looks at tasks completed after that timestamp.
- **CI access via `gh` CLI.** Agent can run `gh run list` / `gh run view` to check what's actually failing. Read-only, low-risk.
- **Yes, differentiate by severity.** "No tests at all" = critical gap (P1 proposal), "unit tests only / over-mocked" = improvement (P2). Both flagged.
- **Grep test content.** Search `tests/integration/` for references to changed files/functions. Content-based matching, not filename convention.
- **No test quality checks in v1.** Scope limited to coverage gaps. Quality checks (empty assertions, assert True) deferred to follow-up.

## Possible Next Steps

- Create the agent config and scripts, following the codebase analyst pattern
- Write the analysis script that scans done tasks and correlates with test files
- Write the guard script (skip if pending testing proposal exists)
- Write the prompt instructing the agent on outside-in testing philosophy
- Test the full flow once the codebase analyst pattern is proven working
