# Unify agent and job configuration

**Captured:** 2026-02-28

## Raw

> this system seems very hard to understand. We run into problems every time I ask for something. eg agents and jobs. Would it be helpful to add a comment to them? Or better yet, have all the agents in one place, but have jobs use some of them in a different manner?
>
> but also, didn't we move to a directory per agent or something? rather than a monolithic agents.yaml?

## Idea

Agent config is currently defined in **three places**:

1. **`.octopoid/agents/<name>/agent.yaml`** — per-agent directory with full config (role, model, max_turns, allowed_tools, etc.)
2. **`.octopoid/agents.yaml`** — monolithic file that re-defines implementer, gatekeeper, fixer (but not the analysts)
3. **`.octopoid/jobs.yaml`** — re-defines the analysts again with inlined `agent_config`

This is the worst of all worlds. The per-agent directories already exist and have everything needed. The monolithic files duplicate and sometimes contradict them (e.g. `agents/implementer/agent.yaml` says `max_turns: 200`, `agents.yaml` says `max_turns: 150`).

### Proposed fix

**The per-agent directories are the source of truth.** The scheduler discovers agents by scanning `.octopoid/agents/*/agent.yaml`. No monolithic `agents.yaml` needed.

Each agent's `agent.yaml` already has: role, model, max_turns, spawn_mode, interval_seconds, allowed_tools. The only thing missing is scheduling metadata — whether it claims tasks or runs on a timer. This can be added directly:

```yaml
# .octopoid/agents/codebase-analyst/agent.yaml
role: analyse
model: opus
max_turns: 30
schedule: daily          # runs once per day (86400s)
spawn_mode: scripts
lightweight: true
allowed_tools: [Read, Glob, Grep, Bash]
```

```yaml
# .octopoid/agents/implementer/agent.yaml
role: implement
model: sonnet
max_turns: 200
max_instances: 2
interval_seconds: 60     # evaluated every 60s
spawn_mode: scripts
allowed_tools: [Read, Write, Edit, Glob, Grep, Bash, Skill]
```

`agents.yaml` becomes either empty (just `paused: false` and `queue_limits:`) or is deleted entirely. `jobs.yaml` keeps only the non-agent periodic scripts (heartbeat, sweep, lease checks).

### What `get_agents()` becomes

```python
def get_agents() -> list[dict]:
    """Discover all agents from .octopoid/agents/*/agent.yaml."""
    agents_dir = get_orchestrator_dir() / "agents"
    agents = []
    for agent_yaml in sorted(agents_dir.glob("*/agent.yaml")):
        config = yaml.safe_load(agent_yaml.read_text())
        config["name"] = agent_yaml.parent.name
        config["agent_dir"] = str(agent_yaml.parent)
        agents.append(config)
    return agents
```

## Invariants

- **single-agent-config**: An agent's configuration exists in exactly one place, and nowhere else. All tools, dashboards, and scheduler logic that need agent information read from that single source.

## Context

Came up while investigating why the codebase analyst hadn't run recently. The analyst agents are defined in `jobs.yaml` as `type: agent` jobs with inlined `agent_config`, which is a different mechanism from the `agents.yaml` agents. This caused confusion — `/agent-status` and the dashboard don't show them, and debugging required knowing to look in `jobs.yaml`.

The per-agent directory structure already exists and was the intended direction, but the scheduler still reads from the monolithic files. The directories were created but the scheduler was never updated to discover from them.

## Open Questions

- Where do `paused: false` and `queue_limits:` live if `agents.yaml` is removed? Probably `.octopoid/config.yaml`.
- Should `jobs.yaml` remain for non-agent periodic scripts, or should those also be discoverable from a directory structure?

## Possible Next Steps

- Update `get_agents()` to scan `.octopoid/agents/*/agent.yaml` instead of reading monolithic `agents.yaml`
- Add `schedule: daily` (or `interval_seconds: 86400`) to analyst `agent.yaml` files
- Remove agent entries from `jobs.yaml`, keeping only script jobs
- Delete or hollow out `agents.yaml` (keep only `paused` and `queue_limits`)
- Update `/agent-status` and dashboard to show all discovered agents
