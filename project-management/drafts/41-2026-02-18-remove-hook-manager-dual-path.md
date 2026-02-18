# Remove Hook Manager: Make Flows the Only Transition Path

**Status:** Idea
**Captured:** 2026-02-18
**Related:** Draft 20 (Flows as Single Integration Path)

## Problem

The scheduler currently has two overlapping mechanisms for controlling task transitions:

1. **Flows** (`flow.py` + `.octopoid/flows/default.yaml`) — declarative state machine with conditions and runs
2. **Hook Manager** (`hook_manager.py`) — imperative `before_merge` / orchestrator hooks that run alongside flows

Both systems can gate the same transitions. The hook manager runs in `check_and_update_finished_agents` (scheduler.py ~lines 1155-1182) and evaluates `before_merge` hooks even though the flow already defines what runs on `provisional -> done`.

This dual path means:
- Two places to look when debugging why a transition didn't fire
- Hooks can silently block a flow transition
- New capabilities get added to whichever system the developer finds first

## Proposal

Migrate everything the hook manager does into flow conditions or runs:
- `before_merge` hooks become conditions on the `provisional -> done` transition
- Orchestrator hooks become script-type conditions
- Delete `hook_manager.py` once all hooks are expressed as flow elements

## Open Questions

- Are there hooks that don't map cleanly to flow conditions? (e.g. hooks that modify data rather than gate transitions)
- Should hook evidence recording be preserved as a flow feature?
