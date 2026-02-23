# Proposed Task: Write testing-analyst prompt and instructions

**Source draft:** 87-2026-02-22-testing-analyst-agent.md
**Proposed role:** implement
**Proposed priority:** P2
**Depends on:** testing-analyst-2-scripts

## Context

Write the prompt.md and instructions.md that guide the Claude agent session. The prompt should embody the outside-in testing philosophy from docs/testing.md and instruct the agent to propose specific, actionable test improvements.

## Acceptance Criteria

- [ ] `prompt.md` created with `$global_instructions` placeholder (substituted at runtime)
- [ ] Prompt follows 7-step workflow matching codebase-analyst:
  1. Run guard.sh — if SKIP, stop immediately
  2. Run scan-test-gaps.sh — get gap report
  3. Pick the single most impactful gap (prioritise "no tests" over "unit tests only")
  4. Analyse the gap — understand what the code does and what test would cover it
  5. Create a draft via SDK (`sdk.drafts.create(title=..., author="testing-analyst", status="idea")`)
  6. Attach action buttons (Enqueue test task / Dismiss)
  7. Post inbox message to notify user
- [ ] Prompt embeds outside-in testing philosophy: prefer e2e with real server > integration > unit. Flag over-mocked tests that test mocks not code.
- [ ] Prompt instructs agent to propose **specific test scenarios** (not vague "add more tests") — include what to test, which fixture to use (`scoped_sdk`), and expected behaviour
- [ ] `instructions.md` created with SDK setup examples, draft creation format, action payload structure, and inbox message format (following codebase-analyst instructions.md pattern)
- [ ] Agent can access CI results via `gh run list` / `gh run view` for additional context
