# Proposed Task: Create testing-analyst agent scaffold

**Source draft:** 87-2026-02-22-testing-analyst-agent.md
**Proposed role:** implement
**Proposed priority:** P2

## Context

First task in the testing-analyst project. Creates the directory structure and agent.yaml following the codebase-analyst pattern. This is the foundation that other tasks build on.

## Acceptance Criteria

- [ ] Directory created: `.octopoid/agents/testing-analyst/`
- [ ] `agent.yaml` created with: `role: analyse`, `model: sonnet`, `max_turns: 30`, `interval_seconds: 86400`, `spawn_mode: scripts`, `lightweight: true`, `allowed_tools: [Read, Glob, Grep, Bash]`
- [ ] Empty `scripts/` directory created
- [ ] Pattern matches codebase-analyst agent.yaml structure exactly
