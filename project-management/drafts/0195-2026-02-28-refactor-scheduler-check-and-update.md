# Refactor scheduler.check_and_update_finished_agents: extract ResultHandler strategy (CCN 20 → ~8)

**Author:** architecture-analyst
**Captured:** 2026-02-28

## Issue

`check_and_update_finished_agents` in `octopoid/scheduler.py` (line 1294) has CCN 20 and 105 lines. The
high complexity comes from a hardcoded 3-way dispatch that determines which result handler to invoke
based on agent type:

```python
if blueprint_name == "fixer" or claim_from == "intervention":
    transitioned = handle_fixer_result(task_id, instance_name, task_dir)
elif claim_from != "incoming":
    transitioned = handle_agent_result_via_flow(task_id, instance_name, task_dir, expected_queue=claim_from)
else:
    transitioned = handle_agent_result(task_id, instance_name, task_dir)
```

This hardcoded dispatch has two problems:
1. **Fragile extension point** — adding a new agent role (e.g. a reviewer) requires modifying
   this core scheduler function, which has unrelated concerns (PID tracking, logging, retry).
2. **Mixed abstractions** — the function both manages PID lifecycle *and* decides which business
   logic to invoke, violating single responsibility.

## Current Code

```python
# scheduler.py:1341-1374 (core dispatch + PID bookkeeping mixed together)
for pid, info in dead_pids.items():
    instance_name = info.get("instance_name", blueprint_name)
    task_id = info.get("task_id", "")
    logger.debug(f"Instance {instance_name} (PID {pid}) has finished")

    if task_id:
        task_dir = get_tasks_dir() / task_id
        if task_dir.exists():
            try:
                # Hardcoded 3-way dispatch — knowledge of handler types leaks here
                if blueprint_name == "fixer" or claim_from == "intervention":
                    transitioned = handle_fixer_result(task_id, instance_name, task_dir)
                elif claim_from != "incoming":
                    transitioned = handle_agent_result_via_flow(...)
                else:
                    transitioned = handle_agent_result(task_id, instance_name, task_dir)
                if transitioned:
                    del pids[pid]
                    ...
            except Exception as e:
                ...
        else:
            del pids[pid]
    else:
        # Background agent path — different bookkeeping
        ...
        del pids[pid]

save_blueprint_pids(blueprint_name, pids)
```

## Proposed Refactoring

Apply the **Strategy pattern**: introduce a `resolve_result_handler` function that maps agent
configuration to the appropriate handler callable. The dispatcher then calls the resolved handler
without knowing which one it is.

```python
# result_handler.py (or a new result_dispatch.py)
from typing import Callable, Protocol

class ResultHandler(Protocol):
    def __call__(self, task_id: str, instance_name: str, task_dir: Path) -> bool: ...

def resolve_result_handler(blueprint_name: str, claim_from: str) -> ResultHandler:
    """Return the correct result handler for this agent type."""
    if blueprint_name == "fixer" or claim_from == "intervention":
        return handle_fixer_result
    if claim_from != "incoming":
        from functools import partial
        return partial(handle_agent_result_via_flow, expected_queue=claim_from)
    return handle_agent_result
```

```python
# scheduler.py:check_and_update_finished_agents — simplified dispatch
handler = resolve_result_handler(blueprint_name, claim_from)

for pid, info in dead_pids.items():
    task_id = info.get("task_id", "")
    if task_id:
        transitioned = _process_finished_task_agent(pid, info, handler, blueprint_name)
    else:
        _process_finished_background_agent(pid, info, blueprint_name, pids)
        continue
    if transitioned:
        del pids[pid]
    ...
```

Extracting `_process_finished_task_agent` and `_process_finished_background_agent` as focused
sub-functions removes the nested conditionals from the main loop, cutting CCN from 20 to ~5.

## Why This Matters

- **Testability** — `resolve_result_handler` can be unit-tested independently: pass in a
  blueprint name and `claim_from`, assert you get the right handler. No scheduler scaffolding needed.
- **Extension** — adding a new agent role means adding one branch in `resolve_result_handler`
  (or registering a handler), not modifying `check_and_update_finished_agents`.
- **Readability** — the main function becomes a clean PID-management loop; business logic
  for each agent role lives in dedicated handlers.
- **Bug prevention** — the current dispatch has a subtle ordering dependency (fixer check
  before claim_from check). Centralising it makes the priority explicit and testable.

## Metrics

- File: `octopoid/scheduler.py`
- Function: `check_and_update_finished_agents`
- Line: 1294
- Current CCN: 20 / Lines: 105
- Estimated CCN after: ~5 for the main function; ~3 for `resolve_result_handler`
