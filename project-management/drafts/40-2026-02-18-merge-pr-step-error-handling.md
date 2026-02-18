# Merge Failures: Orchestrator as Supervisor, Not Steps as Error Handlers

**Status:** Idea
**Captured:** 2026-02-18
**Related:** TASK-31b1fe65 (flow dispatch error recovery), Draft 29 (gatekeeper pure function), Draft 31 (agents as pure functions)

## Raw

> The gatekeeper got stuck in an infinite loop approving TASK-31b1fe65 every 5 minutes for hours. The cause: PR #80 had merge conflicts, so `merge_pr` step silently failed (returned an error dict nobody checked), the task stayed in `provisional`, and the gatekeeper claimed it again next tick. The deeper issue: we're treating "PR can't merge" as an unexpected error when it's a predictable condition that should have a defined flow transition. The step shouldn't be doing lifecycle management, and the orchestrator shouldn't be sending a gatekeeper to review a PR that can't land.

## The Problem

Two layers of failure:

### Layer 1: Silent failure (mechanical bug)

`merge_pr` in steps.py calls `approve_and_merge()` which returns `{"error": "...", "merged": False}` on failure — no exception. The step ignores the return value. The task stays in `provisional`. The gatekeeper claims it again. Infinite loop.

### Layer 2: Wrong design (architectural problem)

Even if the merge step raised properly, the approach is wrong by the pure function / actor model principles established in Drafts 29 and 31:

1. **Reviewing before checking mergeability.** The gatekeeper spent hours reviewing a PR that could never merge. The orchestrator (supervisor) should check mergeability *before* spawning the gatekeeper — don't waste a review on something that can't land.

2. **Steps shouldn't drive lifecycle.** A naive fix puts `sdk.tasks.reject()` and `sdk.tasks.update(needs_rebase=True)` inside the `merge_pr` step. That's the step driving lifecycle — exactly what the pure function model is trying to eliminate. Steps should be pure operations; the flow definition should declare what happens on failure.

3. **Exceptions as flow control.** Turning "PR has conflicts" into a `StepError` that crashes into a catch-all "move to failed" handler treats a normal condition as an unexpected error. In the actor/supervisor model, the supervisor should have a defined strategy for each predictable failure mode.

### Failure modes that need defined transitions (not exceptions)

| Condition | Who detects it | Flow response |
|-----------|---------------|---------------|
| PR has merge conflicts | Guard (pre-gatekeeper) | Skip review, requeue for rebase |
| PR was closed/deleted | Guard (pre-gatekeeper) | Move task to failed |
| Merge fails after approval | Flow runner (post-step) | Requeue for rebase |
| Network error during merge | Flow runner (post-step) | Retry with backoff |

## Proposed Design

### 1. Pre-condition guard: check mergeability before spawning gatekeeper

The guard chain already has `guard_backpressure` checking if provisional tasks exist. Add a guard that checks if the task's PR is actually mergeable before wasting a review:

```python
def guard_pr_mergeable(ctx: AgentContext) -> tuple[bool, str]:
    """Don't review a PR that can't merge."""
    if ctx.claimed_task is None:
        return (True, "")  # No task yet, let claim proceed
    pr_number = ctx.claimed_task.get("pr_number")
    if not pr_number:
        return (True, "")  # No PR, proceed (might be project task)
    mergeable = check_pr_mergeable(pr_number)
    if mergeable == "CONFLICTING":
        # Release claim, set needs_rebase, requeue
        handle_unmergeable(ctx.claimed_task)
        return (False, f"pr_conflicts: PR #{pr_number} needs rebase")
    return (True, "")
```

This runs *after* `guard_claim_task` (so we have the task) but *before* spawning. The supervisor detects the problem and handles it without involving the agent at all.

### 2. Flow-level step error handling

Steps stay pure — they just do the operation and raise if it fails. The flow definition declares the response per-step:

```yaml
provisional_to_done:
  from: provisional
  to: done
  conditions:
    - type: agent
      role: gatekeeper
  runs:
    - post_review_comment
    - merge_pr:
        on_conflict: requeue_for_rebase
        on_error: move_to_failed
```

The flow runner interprets `on_conflict` / `on_error` as transitions, not the step itself. This keeps steps as pure operations and flow control in the declarative definition.

### 3. Steps raise, return structured errors, or both

The minimum mechanical fix: `merge_pr` must not silently swallow failures.

```python
@register_step("merge_pr")
def merge_pr(task: dict, result: dict, task_dir: Path) -> None:
    from . import queue_utils
    merge_result = queue_utils.approve_and_merge(task["id"])
    if merge_result.get("error"):
        raise StepError(f"merge_pr failed: {merge_result['error']}")
```

This is the safety net — if the pre-condition guard somehow misses a conflict, the step raises and the flow runner handles it via the defined `on_error` transition.

## Implementation Sequence

### Phase 1: Stop the bleeding (immediate)
- Make `merge_pr` raise on failure (5 lines in steps.py)
- This alone breaks the infinite loop — the exception triggers TASK-31b1fe65's error recovery (move to failed)
- Not ideal (tasks go to `failed` instead of being handled gracefully) but stops burning credits

### Phase 2: Pre-condition guard (correct fix)
- Add `guard_pr_mergeable` to the gatekeeper's guard chain
- Check mergeability after claim, before spawn
- If conflicting: release claim, set `needs_rebase`, requeue to incoming with feedback
- The gatekeeper never sees unmergeable PRs

### Phase 3: Flow-level error handling (architecture)
- Extend flow YAML syntax with per-step `on_error` / `on_conflict` handlers
- Flow runner dispatches to named transitions instead of catch-all exception handler
- Steps become truly pure — no lifecycle logic, no SDK calls for error recovery

### Phase 4: Auto-rebase (stretch)
- The `needs_rebase` field exists on tasks but nothing acts on it
- Add a housekeeping job that finds tasks with `needs_rebase`, attempts `git rebase`, pushes
- If rebase succeeds: clear the flag, task re-enters the review queue naturally
- If rebase fails (real conflicts): flag for human or requeue to implementer

## What this looks like in actor model terms

- **Agent (gatekeeper)**: Pure function. Receives diff, returns approve/reject. Never sees infrastructure problems.
- **Supervisor (orchestrator)**: Checks pre-conditions before spawning. Handles post-step failures via defined strategies. Manages retries, requeues, escalation.
- **Supervision strategy**: "If merge fails due to conflicts, requeue for rebase (one-for-one restart on clean state). If merge fails for other reasons, move to failed (permanent failure, escalate to human)."
- **Bounded mailbox**: The mergeability guard acts as a filter on the provisional queue — only mergeable tasks get delivered to the gatekeeper's inbox.

## Open Questions

- Should the mergeability check happen in the guard chain (before spawn) or as a flow pre-condition (after claim, before first step)? Guard chain is simpler; flow pre-condition is more general.
- `check_pr_mergeable` requires a GitHub API call. Is that too expensive to run on every scheduler tick? Could cache with short TTL.
- Should auto-rebase be a housekeeping job or a flow step that runs before `merge_pr`?
