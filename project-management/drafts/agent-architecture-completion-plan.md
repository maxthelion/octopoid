# Agent Architecture Completion Plan

## Status Summary

The architecture from `AGENT_ARCHITECTURE_REFACTOR.md` is ~85% implemented. The hooks system from `initialhooks.md` is nearly complete. This plan covers the remaining work to get everything production-ready and cleaned up.

### What's Done
- RepoManager, HookManager, PromptRenderer — all created and working
- All 5 agent scripts (submit-pr, run-tests, finish, fail, record-progress)
- Server-side hooks: migration, API endpoints, evidence recording
- Scheduler: prepare_task_directory(), invoke_claude(), handle_agent_result()
- hooks.py: all built-in hooks with remediation, resolve/run pipeline
- config.py: get_hooks_config(), get_task_types_config(), get_hooks_for_type()
- Task type field in schema, shared types, and migrations
- Implementer.py: full remediation loop (invoke Claude to fix conflicts/test failures)
- Integration + unit tests for hooks and server endpoints

### What's Left
- No unit tests for HookManager
- Scripts mode never been enabled or tested end-to-end
- Scheduler doesn't run orchestrator-side hooks (merge_pr)
- Agent claim filtering by task type not implemented
- env.sh parsing in invoke_claude() is fragile
- Old code (implementer.py, hooks.py legacy parts) not cleaned up
- Documentation outdated in 3 files

---

## Phase A: Testing & Bug Fixes (Before enabling scripts mode)

### A1. Create test_hook_manager.py

Unit tests for HookManager with mocked SDK:
- `resolve_hooks_for_task()` — verify it uses config resolution order (type → project → defaults)
- `resolve_hooks_for_task()` — verify agent vs orchestrator hook classification
- `can_transition()` — all hooks satisfied returns True
- `can_transition()` — pending hooks returns False with list
- `get_pending_hooks()` — filters by point and type
- `run_orchestrator_hook()` — merge_pr success/failure via mocked RepoManager
- `record_evidence()` — calls SDK with correct payload

### A2. Fix env.sh parsing in invoke_claude()

Current code uses naive `string.split("=")` which breaks on values containing `=` or quotes.

Fix: Use `shlex` or source the file in a subprocess and dump the environment:
```python
import shlex
# Parse KEY="value with = signs" correctly
```

### A3. Wire orchestrator-side hooks into scheduler

The scheduler needs to actively run orchestrator hooks (like `merge_pr`) during state transitions. Currently `hook_manager.run_orchestrator_hook()` exists but the scheduler never calls it.

In `scheduler.py`, when processing provisional tasks:
1. Call `hook_manager.get_pending_hooks(task, "before_merge", "orchestrator")`
2. For each pending hook, call `hook_manager.run_orchestrator_hook(task, hook)`
3. Call `hook_manager.record_evidence(task_id, hook_name, evidence)`
4. If `hook_manager.can_transition(task, "done")` → accept the task
5. If hooks fail → log error, leave task in provisional

---

## Phase B: Enable Scripts Mode

### B1. Add agent_mode: scripts to one implementer

In `.orchestrator/agents.yaml`, set `agent_mode: scripts` on `implementer-2` while keeping `implementer-1` on `python` (the default).

### B2. End-to-end test: scripts mode

Manual test checklist:
1. Queue a task (any type)
2. Verify `prepare_task_directory()` creates expected structure:
   - `worktree/` with correct branch
   - `task.json` with task data + hooks
   - `prompt.md` with rendered prompt including required steps
   - `env.sh` with all expected vars
   - `scripts/` with all 5 scripts (correct shebang, executable)
3. Verify `invoke_claude()` launches Claude with correct args
4. Verify Claude can call scripts from within worktree
5. Verify `run-tests` records hook evidence with server
6. Verify `submit-pr` creates PR and records evidence
7. Verify `handle_agent_result()` reads result.json and transitions task
8. Verify scheduler accepts task after orchestrator hooks pass

### B3. Test continuation flow

1. Start a scripts-mode agent on a task
2. Kill the Claude process mid-work
3. Verify scheduler detects the dead process
4. Verify `handle_agent_result()` checks for notes.md / worktree progress
5. Verify task is marked needs_continuation (not failed)

### B4. Test remediation in scripts mode

Note: In scripts mode, remediation works differently than in Python mode. In Python mode, implementer.py runs hooks and invokes Claude again on failure. In scripts mode, the agent IS Claude — so remediation is handled by the prompt telling Claude to retry on failure.

Verify that the prompt includes remediation instructions:
- "If tests fail, fix the issues and run tests again"
- "If rebase conflicts, resolve them and rebase again"

If the prompt doesn't currently handle this, update `orchestrator/prompts/implementer.md` to include remediation instructions for each hook.

---

## Phase C: Agent Claim Filtering by Task Type

### C1. Implement type-based claim filtering

From `initialhooks.md`: "Agent filtering: when claiming tasks, agents check if they're allowed to work on that task's type."

In `scheduler.py` claim logic:
1. Read agent's allowed types from agents.yaml (or from task_types config)
2. When claiming, filter available tasks by type compatibility
3. Unconfigured types remain open to all agents (backward compat)

This requires:
- Adding `allowed_task_types` to agent config in agents.yaml
- Or reading task_types config to find which agents are assigned to each type
- Modifying claim logic in scheduler to pass type filter

### C2. Test type-based filtering

- Agent configured for `product` type only claims product tasks
- Agent with no type restriction claims any task
- Task with unconfigured type is claimable by any agent

---

## Phase D: Cleanup

### D1. Migrate all implementers to scripts mode

After Phase B testing proves scripts mode works:
1. Set `agent_mode: scripts` on all implementer agents
2. Run through several task cycles to confirm stability

### D2. Remove old Python agent code

Once all implementers are on scripts mode:
- Delete `orchestrator/roles/implementer.py` (~950 lines)
- Remove Python-mode spawn path from `scheduler.py`
- Remove PYTHONPATH hacks from scheduler

### D3. Consolidate hooks.py

The old hooks.py has two purposes:
1. Hook definitions and run pipeline (used by implementer.py in Python mode)
2. Constants (BUILTIN_HOOKS, DEFAULT_HOOKS) used by hook_manager.py

After implementer.py is deleted:
- Move BUILTIN_HOOKS and DEFAULT_HOOKS to hook_manager.py or a constants file
- Delete hooks.py entirely
- Update imports

### D4. Slim down base.py

Remove any implementer-specific code from `orchestrator/roles/base.py`. Keep only what other roles (gatekeeper, github_issue_monitor) need.

---

## Phase E: Documentation

### E1. Archive v1 architecture doc

`docs/architecture.md` describes the pre-v2.0 system (SQLite, file-based queues, Python module invocation). Either:
- Rename to `docs/architecture-v1.md` and add "HISTORICAL" header
- Or delete it (architecture-v2.md is the current reference)

### E2. Update v2 implementation status

`docs/v2-IMPLEMENTATION_STATUS.md`: Update test count from 27 to actual count (41+).

### E3. Fix migration guide

`docs/migration-v2.md`: Remove reference to nonexistent `python orchestrator/scripts/export_state.py`. Add note that v1.x state import is manual.

### E4. Update architecture-v2.md for hooks

Add a section to `docs/architecture-v2.md` covering:
- Hook lifecycle (agent hooks vs orchestrator hooks)
- Server-side hook enforcement
- Hook configuration in config.yaml
- Hook evidence recording

### E5. Delete planning docs

Once work is complete, `AGENT_ARCHITECTURE_REFACTOR.md` and `initialhooks.md` can be deleted from root (they're planning docs, not documentation).

---

## Execution Order

```
Phase A (testing/fixes)     — do first, no risk
  A1: test_hook_manager.py
  A2: fix env.sh parsing
  A3: wire orchestrator hooks in scheduler

Phase B (enable scripts)    — needs Phase A
  B1: enable on one agent
  B2: e2e test
  B3: test continuation
  B4: verify remediation in prompts

Phase C (type filtering)    — independent of B, can parallel
  C1: implement claim filtering
  C2: test

Phase D (cleanup)           — needs B confirmed working
  D1: migrate all implementers
  D2: delete implementer.py
  D3: consolidate hooks.py
  D4: slim base.py

Phase E (docs)              — can start anytime, finish after D
  E1-E5: documentation updates
```

## Risk Notes

- **Phase B is the critical gate.** If scripts mode has issues, all cleanup (Phase D) is blocked. Test thoroughly.
- **Remediation gap in scripts mode**: In Python mode, implementer.py programmatically reruns hooks after Claude fixes issues. In scripts mode, Claude IS the agent — remediation depends on the prompt instructing Claude to retry. Make sure the prompt covers this.
- **Phase D is destructive.** Only proceed after multiple successful task cycles in scripts mode. Keep Python mode as fallback until confident.
