# Proposed Task: Write testing-analyst scripts

**Source draft:** 87-2026-02-22-testing-analyst-agent.md
**Proposed role:** implement
**Proposed priority:** P2
**Depends on:** testing-analyst-1-scaffold

## Context

Write the shell scripts that the testing-analyst agent runs before its Claude session. These scripts gather data for the agent to analyse.

## Acceptance Criteria

- [ ] `scripts/guard.sh` — queries SDK for existing testing-analyst drafts with `status='idea'` and `author='testing-analyst'`. Prints `SKIP` if any exist. Fails gracefully if server is unreachable.
- [ ] `scripts/scan-test-gaps.sh` — the main analysis script that:
  - Reads a timestamp file (`.octopoid/runtime/testing-analyst-last-run`) to determine "since last run"
  - Queries done tasks via SDK (tasks completed after the timestamp)
  - For each done task, finds changed files (via git log on the task branch or PR diff)
  - For each changed file, checks whether a corresponding test exists in `tests/` (grep for imports/references)
  - Categorises gaps: "no tests" (critical) vs "unit tests only" (improvement)
  - Outputs a structured report listing: file changed, test status, severity
  - Writes the current timestamp to the last-run file
- [ ] `scripts/reset-timer.sh` — resets the last-run timestamp to epoch so the next run analyses everything. Follow codebase-analyst pattern.
- [ ] All scripts are executable (`chmod +x`)
- [ ] Scripts handle missing files/directories gracefully (first run scenario)
