# Pool model step 1: config format fleet to agents dict

ROLE: implement
PRIORITY: P1

## Context

This is step 1 of 4 for the agent pool model. Currently agents are configured as a named list under `fleet:` in `.octopoid/agents.yaml`. We want to change to a blueprint dict under `agents:` where each key is a blueprint name and `max_instances` controls how many can run concurrently.

A previous attempt (PR #77, now closed) implemented this correctly but went stale. The code logic from that PR is sound — use it as a reference, but work from the current state of `feature/client-server-architecture`.

## Current Format (fleet)

```yaml
fleet:
  - name: implementer-1
    type: implementer
    enabled: true
    interval_seconds: 60
    max_turns: 200
    model: sonnet

  - name: implementer-2
    type: implementer
    enabled: true
    interval_seconds: 60
    max_turns: 200
    model: sonnet

  - name: sanity-check-gatekeeper
    role: gatekeeper
    spawn_mode: scripts
    claim_from: provisional
    interval_seconds: 120
    max_turns: 100
    model: sonnet
    agent_dir: .octopoid/agents/gatekeeper

  - name: github-issue-monitor
    type: custom
    path: .octopoid/agents/github-issue-monitor/
    enabled: false
    interval_seconds: 900
    lightweight: true
```

## Target Format (agents dict)

```yaml
agents:
  implementer:
    type: implementer
    max_instances: 3
    interval_seconds: 60
    max_turns: 200
    model: sonnet

  sanity-check-gatekeeper:
    role: gatekeeper
    spawn_mode: scripts
    agent_dir: .octopoid/agents/gatekeeper
    interval_seconds: 120
    max_turns: 100
    model: sonnet
    max_instances: 1

  github-issue-monitor:
    type: custom
    path: .octopoid/agents/github-issue-monitor/
    enabled: false
    interval_seconds: 900
    max_instances: 1
    lightweight: true
```

**CRITICAL:** Every field from every existing agent entry MUST be preserved. Do NOT drop `spawn_mode`, `agent_dir`, or any other field. The gatekeeper entry is particularly important.

## Files to Change

1. **`.octopoid/agents.yaml`** — Convert from fleet list to agents dict format as shown above. Preserve all existing values (check the current file for what's actually there — the examples above may be outdated).

2. **`orchestrator/config.py`** — Update `get_agents()` to read `agents:` dict instead of `fleet:` list. Each entry should have `blueprint_name` set to the dict key. Add `max_instances` defaulting to 1. Keep backwards compat: if `fleet:` exists and `agents:` does not, read from `fleet:`. Extract agent dir resolution into a helper function to reduce duplication.

3. **`orchestrator/init.py`** — Update the example agents.yaml template (`EXAMPLE_AGENTS_YAML`) to use the new dict format.

4. **Tests** — Add `tests/test_config_get_agents.py` covering: dict format basic case, blueprint_name set to dict key, max_instances defaults, explicit agent_dir, disabled agents excluded, fleet backwards compat, agents dict takes precedence over fleet.

## Do NOT Change

- `orchestrator/scheduler.py` — the scheduler still iterates the list returned by `get_agents()`, this does not change yet (step 2)
- `orchestrator/reports.py` — will be updated in step 4
- `orchestrator/flow.py` — will be updated in step 4

## Acceptance Criteria

- [ ] `.octopoid/agents.yaml` uses `agents:` dict format with all fields preserved
- [ ] `get_agents()` returns a list of dicts, each with `blueprint_name` key
- [ ] Backwards compatible: still reads `fleet:` if `agents:` is absent
- [ ] `max_instances` defaults to 1 for all blueprints
- [ ] All existing tests pass (`pytest tests/`)
- [ ] New tests in `tests/test_config_get_agents.py` pass
