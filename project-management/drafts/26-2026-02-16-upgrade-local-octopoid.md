# Upgrade Local Octopoid to Latest Version

**Status:** Idea
**Captured:** 2026-02-16

## Raw

> We need to look at how we can switch over to the latest version of octopoid in our repo (eg the one we use to manage our work). It's not running the latest. It's a node module, and we might need to re-run init because much has changed in the recent commits. Plan out how we'd do that.

## Idea

The orchestrator running our day-to-day work is using an older version of the octopoid client and Python orchestrator code. The `feature/client-server-architecture` branch has had massive changes — entity module split, hook system, script-based agents, scheduler rewrite, dead code deletion (-7,453 lines in the last merge alone). The running system needs to be updated to use all of this.

## Context

Current state:
- **Client**: `packages/client/` is a local npm package (`octopoid` v2.0.0), built to `dist/`. The `dist/` directory is stale — built from older source.
- **Python orchestrator**: Imported directly from `orchestrator/` in the repo. Since we're on `feature/client-server-architecture`, the Python code IS the latest (it's the same files). But the scheduler process may have been started before recent changes, so it's running old code in memory.
- **Config**: `.octopoid/config.yaml` has `main_branch: feature/client-server-architecture` — correct for now but will need to change to `main` after the branch merge.
- **Agents**: `.octopoid/agents.yaml` defines the agent configs. The recent merge brought in new agent directory structure (`.octopoid/agents/implementer/`, `.octopoid/agents/gatekeeper/`).
- **Server**: Running on Cloudflare Workers, deployed separately. The `submodules/server` is pinned to `feat/task-branch-inheritance` — may also need updating.

## What Changed That Matters

1. **Entity module split** — `queue_utils.py` split into `sdk.py`, `tasks.py`, `projects.py`, etc. Any running Python process importing the old monolith needs restarting.
2. **Script-based agent architecture** — agents now use scripts in `.octopoid/agents/<name>/scripts/` instead of inline TypeScript roles. The scheduler spawns `claude` directly instead of going through the client package.
3. **Hook system** — `hooks.py` + `hook_manager.py` with BEFORE_SUBMIT and BEFORE_MERGE. Existing tasks in the queue may not have hook evidence.
4. **Deleted roles** — `roles/proposer.py`, `roles/curator.py`, `roles/gatekeeper.py`, etc. all deleted. If anything references them, it'll break.
5. **Agent directory structure** — moved from `orchestrator/agent_scripts/` to `.octopoid/agents/implementer/scripts/`. The scheduler needs to find scripts at the new paths.

## Upgrade Plan

### Step 1: Pause the system
```
/pause-system
```
Stop the scheduler so no agents are mid-flight during the upgrade.

### Step 2: Rebuild the client package
```bash
cd packages/client && pnpm install && pnpm build
```
This rebuilds `dist/` from the latest TypeScript source. The `octopoid init` command lives here.

### Step 3: Re-run init (if needed)
```bash
npx octopoid init
```
This should detect existing config and offer to update. Key things init sets up:
- `.octopoid/config.yaml` — may need new fields
- `.octopoid/agents.yaml` — agent definitions
- Agent directories with scripts

Check what `init` does now vs what we already have. It may try to overwrite our config. Probably safer to diff the template against our existing config and manually merge.

### Step 4: Restart the scheduler
The Python scheduler imports modules at startup. It needs a fresh start to pick up:
- Entity module split (sdk.py, tasks.py, etc.)
- Hook system
- New agent script paths

```bash
# Kill any running scheduler
pkill -f "python.*scheduler"
# Restart
python -m orchestrator.scheduler &
```

### Step 5: Verify agents can be spawned
```
/agent-status
```
Check that agents are found at the new paths and can claim tasks.

### Step 6: Update server submodule (if needed)
```bash
cd submodules/server
git fetch origin
git checkout main  # or latest tag
cd ../..
git add submodules/server
```
Deploy if the server schema has changed.

### Step 7: Resume the system
```
/pause-system  # toggles back to unpaused
```

## Open Questions

- Does `octopoid init` handle upgrades gracefully, or does it only work for fresh installs? We may need an `octopoid upgrade` command.
- Are there database migrations needed on the server side?
- Should we snapshot the current queue state before upgrading, in case something breaks?
- The scheduler is a long-running Python process — is there a systemd/launchd service managing it, or is it just a background process?

## Possible Next Steps

- Try the upgrade on a branch first to validate the process
- Document the upgrade steps so future upgrades are repeatable
- Consider adding a version check to the scheduler that warns if code has changed since startup
