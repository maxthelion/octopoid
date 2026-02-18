# Local Upgrade Guide

How to upgrade the octopoid installation running in this repo to the latest code.

## Prerequisites

- System is paused (`/pause-system`) — no agents mid-flight
- You're on the branch with the latest code (currently `feature/client-server-architecture`, eventually `main`)

## Steps

### 1. Pause the system

```
/pause-system
```

Wait for any running agents to finish. Check with `/agent-status`.

### 2. Pull latest code

```bash
git pull origin <branch>
```

### 3. Rebuild the client package

```bash
cd packages/client && pnpm install && pnpm build && cd ../..
```

This rebuilds `dist/` from the latest TypeScript source.

### 4. Update Python dependencies

```bash
pip install -e packages/python-sdk
```

Ensures the SDK package matches the latest source.

### 5. Check config for new fields

Compare your `.octopoid/config.yaml` against the template:

```bash
diff .octopoid/config.yaml packages/client/templates/config.yaml
```

Add any new required fields manually. Don't overwrite — your config has environment-specific values (server URL, machine ID, etc.).

### 6. Check agents.yaml for new agent types

```bash
diff .octopoid/agents.yaml packages/client/templates/agents.yaml
```

New agent types (e.g. gatekeeper) may need adding. Existing agent configs should be preserved.

### 7. Update agent scripts

Agent scripts live in `.octopoid/agents/<name>/scripts/`. If the templates have changed:

```bash
# Compare
diff -r .octopoid/agents/implementer/scripts packages/client/agents/implementer/scripts

# Copy updated scripts (preserves your custom ones)
cp -n packages/client/agents/implementer/scripts/* .octopoid/agents/implementer/scripts/
```

### 8. Restart the scheduler

The scheduler is a long-running Python process that imports modules at startup. It must be restarted to pick up code changes.

```bash
# Find and kill the running scheduler
pkill -f "python.*scheduler"

# Restart (however you normally run it)
python -m orchestrator.scheduler &
```

### 9. Update server submodule (if needed)

Only needed if the server API has changed (new endpoints, schema migrations):

```bash
cd submodules/server
git fetch origin
git checkout main
cd ../..
pnpm --filter @octopoid/server run deploy
```

### 10. Resume the system

```
/pause-system
```

### 11. Verify

```
/agent-status    # agents found and idle
/queue-status    # tasks in expected queues
```

## When to upgrade

- After merging a feature branch with structural changes (module splits, new agent types, scheduler changes)
- After server schema changes (new columns, new endpoints)
- After changing agent scripts or hook definitions

## What doesn't need an upgrade

- Changes to task files (`.octopoid/tasks/`) — read fresh each time
- Changes to draft files (`project-management/drafts/`) — just files on disk
- Server-side changes deployed via `wrangler deploy` — independent of local code
- Config changes to `.octopoid/config.yaml` or `.octopoid/agents.yaml` — read on each scheduler tick
