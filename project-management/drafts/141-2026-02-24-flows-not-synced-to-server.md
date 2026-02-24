# Flows Not Synced to Server

**Status:** Idea
**Captured:** 2026-02-24

## Raw

> Flows don't seem to be wired into the scheduler. We tried using a different flow in boxen, and it didn't use it. Check whether they are all available. The Work tab in the dashboard only shows a single flow, but there should be several.

## Idea

There's a disconnect between the local flow YAML files and the server's flow registry. The scheduler reads flows from local YAML files (`.octopoid/flows/*.yaml`), but the dashboard fetches flows from the server API (`sdk.flows.list()`). Nothing syncs local flows to the server.

## Investigation Findings

### What exists locally

Two flow YAML files in `.octopoid/flows/`:
- `default.yaml` — incoming → claimed → provisional → done (with gatekeeper review)
- `project.yaml` — child tasks + project-level PR creation and merge

### What the server knows about

Only **one flow** registered on the server: `default` with states `["claimed","done","failed","incoming","provisional"]`.

The `project` flow is **not registered on the server**, so the dashboard's Work tab can't show it.

### The gap

- **`sdk.flows.register()`** exists in the SDK and is tested in integration tests
- **Nothing calls it** — not the scheduler, not `init.py`, not any startup code
- The scheduler's `load_flow()` reads from local YAML files and works fine for transition logic
- But `reports.py:_gather_flows()` calls `sdk.flows.list()` for the dashboard, which only sees server-registered flows
- Custom flows created in other instances (like boxen) would never reach the server

### Why custom flows don't work in boxen

If someone creates a custom `.octopoid/flows/foo.yaml` and sets `flow: foo` on a task:
- The scheduler **will** load it locally and execute transitions correctly
- The dashboard **won't** show it as a tab in the Work view (server doesn't know about it)
- If the server validates queue names against registered flows, the task might get rejected

## Proposed Fix

Add a `sync_flows_to_server()` function that runs at scheduler startup:

1. Read all `.octopoid/flows/*.yaml` files
2. Parse each into states and transitions
3. Call `sdk.flows.register()` for each one
4. The register endpoint should be idempotent (upsert)

Call it from `scheduler.py:main()` during initialization, alongside `_check_venv_integrity()` and `_clear_pycache()`.

Also wire it into `init.py` so that `octopoid init` registers flows when scaffolding a new project.

## Open Questions

- Should `sdk.flows.register()` be an upsert (create-or-update), or does it already handle that?
- Should the scheduler sync flows on every tick, or only on startup?
- Should the dashboard fall back to reading local YAML if the server has no flows registered?

## Possible Next Steps

- Add `sync_flows_to_server()` to `orchestrator/scheduler.py` startup
- Add flow registration to `orchestrator/init.py`
- Verify `sdk.flows.register()` handles upsert correctly
- Could be a quick direct fix or a small task
