# Proposed Task: Register testing-analyst job and verify

**Source draft:** 87-2026-02-22-testing-analyst-agent.md
**Proposed role:** implement
**Proposed priority:** P2
**Depends on:** testing-analyst-3-prompt

## Context

Final task — register the testing-analyst as a scheduled job in jobs.yaml and verify it runs correctly end-to-end.

## Acceptance Criteria

- [ ] Job added to `.octopoid/jobs.yaml`:
  ```yaml
  - name: testing_analyst
    interval: 86400
    type: agent
    group: remote
    max_instances: 1
    agent_config:
      role: analyse
      spawn_mode: scripts
      lightweight: true
      agent_dir: .octopoid/agents/testing-analyst
  ```
- [ ] Manual test: run `reset-timer.sh`, then trigger a scheduler tick. Agent should:
  - Run guard.sh (no existing proposals → proceed)
  - Run scan-test-gaps.sh (produces report)
  - Create a draft with a specific test proposal
  - Post to inbox
- [ ] Guard script works: run again immediately — agent should SKIP (proposal already pending)
- [ ] CHANGELOG.md updated with new background agent entry
