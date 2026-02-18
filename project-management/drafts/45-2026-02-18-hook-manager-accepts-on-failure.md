# Postmortem: Task accepted despite merge_pr failure

**Status:** Idea
**Captured:** 2026-02-18
**Severity:** P0 — tasks marked done with unmerged PRs
**Related:** Draft 41 (Remove Hook Manager), Draft 44 (Unify Result Handlers)

## Incident

TASK-6ee319d0 (PR #88, detached HEAD fix) was moved to `done` queue even though its PR was never merged. The PR had `base: main` (wrong, should be `feature/client-server-architecture`) and was `DIRTY`/`CONFLICTING`. The gatekeeper never reviewed it. The `merge_pr` hook failed, but the task was accepted anyway.

This left all implementers broken for hours — they kept failing to spawn because the fix they depended on was "done" but not actually applied.

## Root Cause

In `process_orchestrator_hooks()` (scheduler.py ~line 1209):

```python
for hook in pending:
    evidence = hook_manager.run_orchestrator_hook(task, hook)
    hook_manager.record_evidence(task_id, hook["name"], evidence)

    if evidence.status == "failed":
        break  # exits loop but continues to can_transition check

# Re-fetch and check
updated_task = sdk.tasks.get(task_id)
can_accept, still_pending = hook_manager.can_transition(updated_task, "before_merge")
if can_accept:
    sdk.tasks.accept(...)  # ← accepts despite failure!
```

`can_transition()` in hook_manager.py:

```python
def can_transition(self, task, target_point):
    pending = self.get_pending_hooks(task, point=target_point)
    return len(pending) == 0, pending_names
```

`get_pending_hooks()` filters for `status == "pending"`. After `record_evidence` writes `status: "failed"`, the hook is no longer "pending" — so `can_transition` returns `True` because there are zero pending hooks. **It doesn't check for failed hooks.**

## Why this matters

This is not an edge case. Any provisional task where `merge_pr` fails (conflicts, wrong base, GitHub API error) will be silently accepted as done. The task disappears from the queue, the PR stays unmerged, and the work is effectively lost.

## Immediate fix

`can_transition` should return `False` if any hook has `status == "failed"`:

```python
def can_transition(self, task, target_point):
    hooks = self._get_hooks(task)
    relevant = [h for h in hooks if h.get("point") == target_point]
    failed = [h for h in relevant if h.get("status") == "failed"]
    if failed:
        return False, [h["name"] for h in failed]
    pending = [h for h in relevant if h.get("status") == "pending"]
    return len(pending) == 0, [h["name"] for h in pending]
```

## Deeper fix

This entire subsystem (`process_orchestrator_hooks` + `HookManager`) is the legacy dual path from Draft 41. It runs independently of flows, polls provisional tasks every tick, and has this logic bug. Draft 44 proposes replacing it with a single flow-driven handler where the merge step is just another `runs` entry — if it fails, the flow's `on_fail` determines what happens (requeue, fail, etc).

The right fix is to delete `process_orchestrator_hooks` entirely (Draft 41) and ensure the flow-based path (`handle_agent_result_via_flow`) handles merge failures correctly — which it already does via TASK-37b6e117's fix (merge_pr raises on failure, caught by flow dispatch error handling).

## Timeline

1. TASK-6ee319d0 claimed by implementer-1, worked on, submitted with PR #88
2. PR #88 created with wrong base (`main` instead of `feature/client-server-architecture`) — the same `create_pr` bug it was trying to help fix
3. Task moved to provisional, `process_orchestrator_hooks` runs on next tick
4. `merge_pr` hook fails (PR has conflicts due to wrong base)
5. `can_transition` returns True because failed != pending
6. Task accepted as done
7. Both implementers keep failing to spawn on every subsequent tick — the fix they need is "done" but not merged
8. Manual intervention required to fix base branch and merge PR

## Action items

- [ ] Fix `can_transition` to check for failed hooks (immediate, P0)
- [ ] Delete `process_orchestrator_hooks` once flow path handles all cases (Draft 41)
- [ ] The `create_pr` base branch bug (TASK-e37bc845) is still the upstream cause — it creates PRs against `main` instead of the task's branch
