# Agent Pool Model: Blueprints Instead of Named Instances

**Status:** Idea
**Captured:** 2026-02-16
**Related:** Draft 20 (Flows as Single Integration Path)

## Raw

> Why do we need impl-1 and impl-2? Surely there should be a single agent blueprint that gets spawned as many times as needed for claims? Am I missing something?

## Idea

Replace the current static agent model (pre-declared named instances in `agents.yaml`) with a pool model: define agent **blueprints** and let the scheduler spawn instances dynamically based on demand.

### Current model (static, named instances)

```yaml
agents:
  - name: implementer-1
    role: implementer
    interval_seconds: 180
  - name: implementer-2
    role: implementer
    interval_seconds: 180
  - name: sanity-check-gatekeeper
    type: custom
    interval_seconds: 300
```

Each instance has its own state file, worktree, PID tracking, failure counter. The scheduler iterates over them one by one. Scaling means editing yaml and restarting.

### Proposed model (blueprints + pool)

```yaml
agents:
  implementer:
    type: scripts
    max_instances: 3
    scripts_dir: .octopoid/agents/implementer/scripts
    model: sonnet

  sanity-check-gatekeeper:
    type: custom
    max_instances: 1
    scripts_dir: .octopoid/agents/sanity-check-gatekeeper/scripts
    model: opus
```

The scheduler sees N claimable tasks that need an `implementer`, checks how many implementer instances are currently running, and spawns more up to `max_instances`. No pre-naming. Instances are ephemeral — they exist for the duration of a task, then release.

### What this fixes

- **Manual scaling**: Change `max_instances: 5` instead of adding `implementer-3`, `implementer-4`, `implementer-5` to yaml
- **Stale state**: No persistent named state files that accumulate garbage. Instance state lives only while the task is active
- **Misleading failure counters**: Currently implementer-2 has 18 consecutive failures — but failures are task-level, not agent-level. A pool doesn't carry per-instance baggage
- **Meaningless names**: `implementer-1` and `implementer-2` are identical configurations. The names serve no purpose

### How it connects to flows

Flows (Draft 20) declare which agent role handles each transition:

```yaml
"incoming -> claimed":
  agent: implementer
```

The scheduler resolves `implementer` by looking up the blueprint, checking capacity, and spawning an instance. The flow doesn't care about instance names — it only cares about the role.

### Instance identity

Instances still need identity for:
- Worktree paths (use task ID, not agent name — we already do this)
- Server-side claim tracking (`claimed_by` field) — use `implementer/TASK-abc123` or just the task ID
- PID tracking — tied to the task, not the agent
- Logs — per-task, not per-agent-instance

### Scheduler loop change

Current:
```
for each named agent in agents.yaml:
  if agent is not running:
    try to claim a task for this agent's role
    if claimed: spawn agent
```

Proposed:
```
for each blueprint in agents.yaml:
  running_count = count instances running this blueprint
  if running_count < max_instances:
    claimable = tasks needing this blueprint's role
    if claimable:
      claim task
      spawn instance
```

### Config migration

The `agents.yaml` format changes. Old format lists instances, new format lists blueprints. This is a breaking change to the config file but not to the runtime — the scheduler just reads the new format.

## Decisions

1. **`max_instances`**: Per-blueprint, with global `max_concurrent` cap (already exists in config as `agents.max_concurrent`).
2. **Orchestrator identity**: Register the scheduler once per machine (using `machine_id` from config). Delete per-agent registration. Use blueprint name (e.g. `implementer`) for `claimed_by` on tasks. Individual instances are just subprocesses — debug via PID/task ID, not server registration.
3. **Stats**: Per-task (server-side task history), not per-blueprint or per-instance. No local state files.
4. **Backpressure**: Unchanged — global `max_open_prs` check before spawning. Already instance-agnostic.

## Possible Next Steps

- Prototype the new agents.yaml format and scheduler loop
- Migrate existing agents.yaml to blueprint format
- Remove per-instance state files, replace with per-task state
- Update the `/agent-status` skill to show pool status instead of named instances
