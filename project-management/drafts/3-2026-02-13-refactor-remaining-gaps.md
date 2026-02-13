# Agent Architecture Refactor: Remaining Gaps

**Status:** Idea
**Captured:** 2026-02-13

## Raw

> "Minor gaps:
>   - agent_mode: scripts flag from the plan was never added — it's hardcoded for all implementers (no backward
>   compat needed since the old code is gone)" re REFACTOR_PLAN. Detail what needs to be done

## Idea

The agent architecture refactor (from Python module invocation to script-based `claude -p` agents) is ~95% complete. The core pieces work — task directories, prompt rendering, agent scripts, result handling, hook manager, repo manager. But testing the live system revealed several gaps that prevent agents from actually completing work.

## Active Bugs (blocking agent execution)

### 1. `CLAUDECODE` env var blocks nested sessions
**File:** `orchestrator/scheduler.py:838`

`invoke_claude()` copies `os.environ` which includes `CLAUDECODE` set by any parent Claude session. The spawned `claude -p` detects this and refuses to start with: `"Error: Claude Code cannot be launched inside another Claude Code session."`

**Fix:** Unset `CLAUDECODE` from the env dict before spawning:
```python
env = os.environ.copy()
env.pop("CLAUDECODE", None)
```

### 2. `OCTOPOID_SERVER_URL` is empty in env.sh
**File:** `orchestrator/scheduler.py:762`

The scheduler writes:
```python
f"export OCTOPOID_SERVER_URL='{os.environ.get('OCTOPOID_SERVER_URL', '')}'"
```

But the URL comes from `.octopoid/config.yaml`, not the environment. When the scheduler runs from a terminal, `OCTOPOID_SERVER_URL` isn't set, so env.sh gets an empty string. Agent scripts can't talk to the server to record hook evidence.

**Fix:** Read from config instead:
```python
from .config import load_config  # or however config is loaded
server_url = os.environ.get('OCTOPOID_SERVER_URL') or config.get('server', {}).get('url', '')
```

### 3. No result.json written on crash
When `claude -p` fails immediately (as with the CLAUDECODE issue), no `result.json` is written. The scheduler's `handle_agent_result()` needs to handle this gracefully — currently it may leave the task stuck in claimed.

**Check:** Does `handle_agent_result()` already handle missing result.json? It should fail the task or requeue it.

## Cleanup Items (non-blocking)

### 4. `agent_mode` flag never added
The refactor plan called for `agent_mode: scripts` in agents.yaml as a feature flag with backward compat to `python` mode. This was never implemented — the scheduler hardcodes scripts mode for all implementers. Since `roles/implementer.py` is a 3-line stub now, there's no backward compat to maintain.

**Decision:** Either add the flag for completeness (other roles might want it), or remove the mention from the refactor plan and close it out. The hardcoded check (`if role == "implementer"`) should probably be cleaned up regardless.

### 5. AGENT_ARCHITECTURE_REFACTOR.md is stale
The draft at `project-management/drafts/AGENT_ARCHITECTURE_REFACTOR.md` describes the full plan. It's now 95% implemented. Should be updated to reflect current status or archived.

## Context

Discovered while running the scheduler with `--once`. implementer-1 claimed gh-13 and created the full task directory (worktree, scripts, prompt, env.sh), but `claude -p` immediately exited because of the `CLAUDECODE` env var. The empty `OCTOPOID_SERVER_URL` was found by inspecting env.sh.

## Open Questions

- Should the scheduler detect and log when `claude -p` fails immediately (exit within seconds)?
- Should env.sh source from config.yaml directly, or should the scheduler always set `OCTOPOID_SERVER_URL` in the process env before spawning?

## Possible Next Steps

- Fix bugs #1 and #2 (quick, 2 lines each)
- Verify #3 (result handling for missing result.json)
- Rerun scheduler and confirm an agent completes a task end-to-end
- Clean up #4 and #5 as follow-up
